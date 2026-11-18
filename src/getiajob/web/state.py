"""État partagé de l'application : démarrage, session utilisateur, navigation."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import streamlit as st
import streamlit.components.v1 as components

from getiajob.config import get_settings
from getiajob.db import READ_ONLY
from getiajob.services.admin import (
    end_impersonation,
    impersonation_target,
    impersonation_write_mode,
    is_admin,
)
from getiajob.services.runner import BackgroundRunner
from getiajob.services.users import (
    AuthError,
    SessionUser,
    create_auth_session,
    get_or_create_oidc_user,
    revoke_auth_session,
    user_from_token,
)

USER_KEY = "user"
PAGES_KEY = "_pages"
SSO_ERROR_KEY = "_sso_error"  # refus de création d'un compte SSO (affiché sur la page de connexion)
TOKEN_KEY = "_session_token"
COOKIE_OP_KEY = "_cookie_op"
COOKIE_NAME = "getiajob_session"
OPEN_SIDEBAR_KEY = "_open_sidebar"
IMPERSONATOR_KEY = "_impersonator"  # administrateur qui consulte le compte courant en lecture seule
WRITE_MODE_KEY = "_impersonation_write"  # consultation avec modifications autorisées (mode édition)


@st.cache_resource(show_spinner="Initialisation…")
def get_runner() -> BackgroundRunner:
    """Exécuté une fois par processus serveur : logs, base de données, files de tâches de fond."""
    from getiajob.db import init_db

    logging.basicConfig(
        level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s — %(message)s"
    )
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    init_db()
    return BackgroundRunner()


# --------------------------------------------------------------------------- #
# Session utilisateur
# --------------------------------------------------------------------------- #


def current_user() -> SessionUser | None:
    return st.session_state.get(USER_KEY)


def require_user() -> SessionUser:
    user = current_user()
    if user is None:
        st.warning("Connecte-toi pour accéder à cette page.")
        st.stop()
    return user


def sync_admin_flag() -> SessionUser | None:
    """Relit le rôle administrateur en base à chaque exécution (droits donnés ou retirés à chaud).

    Pendant une consultation en lecture seule, le compte consulté n'a jamais le menu Admin.
    """
    user = current_user()
    if user is not None and impersonator() is not None:
        if user.is_admin:
            user = replace(user, is_admin=False)
            st.session_state[USER_KEY] = user
        return user
    if user is not None and (admin := is_admin(user.id)) != user.is_admin:
        user = replace(user, is_admin=admin)
        st.session_state[USER_KEY] = user
    return user


def require_admin() -> SessionUser:
    user = require_user()
    if not user.is_admin:
        st.error("Cette page est réservée aux administrateurs.", icon=":material/block:")
        st.stop()
    return user


def sign_in(user: SessionUser, *, remember: bool = True) -> None:
    st.session_state[USER_KEY] = user
    st.session_state[OPEN_SIDEBAR_KEY] = True
    if remember:
        token = create_auth_session(user.id, get_settings().session_days)
        st.session_state[TOKEN_KEY] = token
        st.session_state[COOKIE_OP_KEY] = ("set", token)


def refresh_user(user: SessionUser) -> None:
    """Met à jour l'utilisateur en session (ex: changement de nom) sans recréer de jeton."""
    st.session_state[USER_KEY] = user


# --------------------------------------------------------------------------- #
# Consultation d'un compte en lecture seule (administrateurs)
# --------------------------------------------------------------------------- #

# Clés de session conservées quand on entre dans / sort d'un compte consulté
_KEEP_KEYS = {TOKEN_KEY, PAGES_KEY}


def is_read_only() -> bool:
    return impersonator() is not None and not is_write_mode()


def is_write_mode() -> bool:
    """L'administrateur agit sur le compte consulté comme s'il était l'utilisateur."""
    return impersonator() is not None and bool(st.session_state.get(WRITE_MODE_KEY))


def set_write_mode(enabled: bool) -> None:
    """Bascule la consultation entre lecture seule et mode édition (vérifié et tracé)."""
    admin, target = impersonator(), current_user()
    if admin is None or target is None or enabled == is_write_mode():
        return
    try:
        impersonation_write_mode(admin.id, target.email, enabled)
    except AuthError:
        enabled = False
    st.session_state[WRITE_MODE_KEY] = enabled
    READ_ONLY.set(not enabled)


def impersonator() -> SessionUser | None:
    return st.session_state.get(IMPERSONATOR_KEY)


def apply_read_only() -> bool:
    """À appeler à chaque exécution : interdit toute écriture en base pendant une consultation,
    sauf en mode édition (et seulement tant que l'administrateur a toujours ce rôle)."""
    admin = impersonator()
    if is_write_mode() and admin is not None and not is_admin(admin.id):
        st.session_state[WRITE_MODE_KEY] = False
    read_only = is_read_only()
    READ_ONLY.set(read_only)
    return read_only


def _reset_view_state() -> None:
    for key in list(st.session_state.keys()):
        if key not in _KEEP_KEYS and key not in (USER_KEY, IMPERSONATOR_KEY):
            del st.session_state[key]


def start_impersonation(admin: SessionUser, user_id: int) -> SessionUser:
    """L'administrateur consulte le compte `user_id` en lecture seule (son propre cookie est conservé)."""
    if impersonator() is not None:
        raise RuntimeError("Une consultation est déjà en cours.")
    target = impersonation_target(admin.id, user_id)
    _reset_view_state()
    st.session_state[IMPERSONATOR_KEY] = admin
    st.session_state[USER_KEY] = replace(target, is_admin=False)
    READ_ONLY.set(True)
    return target


def stop_impersonation() -> None:
    """Revient au compte de l'administrateur."""
    admin = st.session_state.pop(IMPERSONATOR_KEY, None)
    st.session_state.pop(WRITE_MODE_KEY, None)
    READ_ONLY.set(False)
    if admin is None:
        return
    target = current_user()
    _reset_view_state()
    st.session_state[USER_KEY] = admin
    if target is not None:
        end_impersonation(admin.id, target.email)


def sign_out() -> None:
    stop_impersonation()  # se déconnecter pendant une consultation déconnecte aussi l'administrateur
    user = current_user()
    if token := st.session_state.get(TOKEN_KEY) or st.context.cookies.get(COOKIE_NAME):
        revoke_auth_session(token)
    st.session_state.clear()
    st.session_state[COOKIE_OP_KEY] = ("clear", None)
    if user and user.provider != "local" and oidc_enabled():
        st.logout()  # supprime aussi le cookie d'identité OIDC
    else:
        st.rerun()


def restore_session() -> None:
    """Reconnecte automatiquement l'utilisateur à partir du cookie « rester connecté »."""
    if current_user() is not None or st.session_state.get(COOKIE_OP_KEY):
        return
    token = st.context.cookies.get(COOKIE_NAME)
    if token and (user := user_from_token(token)):
        st.session_state[USER_KEY] = user
        st.session_state[TOKEN_KEY] = token
        st.session_state[OPEN_SIDEBAR_KEY] = True


def apply_cookie_ops() -> None:
    """Dépose ou efface le cookie de session côté navigateur (Streamlit ne sait pas le faire en Python)."""
    op = st.session_state.pop(COOKIE_OP_KEY, None)
    if not op:
        return
    action, token = op
    secure = "; Secure" if str(getattr(st.context, "url", "")).startswith("https") else ""
    if action == "set":
        max_age = get_settings().session_days * 86400
        cookie = f"{COOKIE_NAME}={token}; Path=/; Max-Age={max_age}; SameSite=Strict{secure}"
    else:
        cookie = f"{COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Strict{secure}"
    components.html(f"<script>window.parent.document.cookie = {json.dumps(cookie)};</script>", height=0)


def open_sidebar_after_login() -> None:
    """Déplie le menu latéral juste après la connexion.

    Streamlit garde l'état replié du menu pendant toute la session du navigateur : s'il a été
    fermé avant une déconnexion, il resterait fermé à la reconnexion. On clique donc sur le
    bouton de dépliage s'il est présent (sauf sur mobile, où le menu recouvrirait la page).
    """
    if not st.session_state.pop(OPEN_SIDEBAR_KEY, False):
        return
    components.html(
        """<script>
        const doc = window.parent.document;
        let tries = 0;
        const timer = setInterval(() => {
          tries += 1;
          const expand = doc.querySelector('[data-testid="stExpandSidebarButton"]');
          const opened = doc.querySelector('[data-testid="stSidebarCollapseButton"]');
          if (expand && window.parent.innerWidth >= 768) expand.click();
          if (expand || opened || tries > 30) clearInterval(timer);
        }, 100);
        </script>""",
        height=0,
    )


def oidc_enabled() -> bool:
    """Connexion SSO (Google, Microsoft…) active si une section [auth] existe dans secrets.toml."""
    try:
        return "auth" in st.secrets
    except Exception:
        return False


def sync_oidc_user() -> None:
    """Après un st.login() réussi, crée/charge l'utilisateur correspondant en base."""
    if current_user() is not None or not oidc_enabled() or not st.user.is_logged_in:
        return
    email = st.user.get("email")
    if email:
        try:
            user = get_or_create_oidc_user(email, st.user.get("name"), provider="oidc")
        except AuthError as exc:  # ex. adhésion sur invitation sans invitation pour cette adresse
            st.session_state[SSO_ERROR_KEY] = str(exc)
            return
        st.session_state.pop(SSO_ERROR_KEY, None)
        sign_in(user, remember=False)


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #


def register_pages(pages: dict[str, st.Page]) -> None:
    st.session_state[PAGES_KEY] = pages


def go(page_key: str) -> None:
    st.switch_page(st.session_state[PAGES_KEY][page_key])


GOTO_KEY = "_goto"


def go_after_rerun(page_key: str) -> None:
    """Ouvre une page qui n'existe pas encore dans le menu courant (ex. Admin, après une consultation)."""
    st.session_state[GOTO_KEY] = page_key
    st.rerun()


def pending_goto() -> None:
    """À appeler une fois le menu construit : effectue la navigation demandée par go_after_rerun."""
    if (key := st.session_state.pop(GOTO_KEY, None)) and key in st.session_state.get(PAGES_KEY, {}):
        go(key)


PROFILE_SECTION_KEY = "profile_section"
APPLICATION_FOCUS_KEY = "applications_focus"  # candidature à déplier à l'ouverture de « Mes candidatures »


def go_profile(section: str = "cv") -> None:
    """Ouvre « Mon profil » sur l'onglet voulu : « cv » ou « fiche »."""
    st.session_state[PROFILE_SECTION_KEY] = section
    go("profile")


def page(page_key: str) -> st.Page:
    return st.session_state[PAGES_KEY][page_key]
