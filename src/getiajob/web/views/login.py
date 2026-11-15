"""Connexion / création de compte."""

from __future__ import annotations

import streamlit as st

from getiajob.config import get_settings
from getiajob.services.access import check_invitation, invitation_required
from getiajob.services.users import AuthError, authenticate, create_user
from getiajob.web.state import SSO_ERROR_KEY, current_user, go, oidc_enabled, sign_in
from getiajob.web.views.access_request import request_form

LOGIN, SIGNUP = "Connexion", "Créer un compte"
MODE_KEY = "login_mode"
INVITE_KEY = "_invitation_code"  # code d'invitation reçu dans le lien (?invitation=…)


def render() -> None:
    if current_user():
        go("dashboard")

    _, center, _ = st.columns([1, 2, 1])
    with center:
        st.markdown("### Bienvenue")
        st.caption("Connecte-toi pour gérer ta fiche, ton CV et tes recherches.")

        if sso_error := st.session_state.get(SSO_ERROR_KEY):
            st.error(sso_error, icon=":material/block:")
            if st.button("Se déconnecter du SSO", icon=":material/logout:"):
                st.session_state.pop(SSO_ERROR_KEY, None)
                st.logout()

        if oidc_enabled():
            st.button(
                "Continuer avec mon compte SSO",
                icon=":material/key:",
                on_click=st.login,
                use_container_width=True,
            )
            st.markdown("<p style='text-align:center;opacity:.6'>ou</p>", unsafe_allow_html=True)

        allow_signup = get_settings().allow_signup
        # Lien d'invitation : on retient le code et on ouvre directement « Créer un compte ».
        if code := st.query_params.get("invitation"):
            st.session_state[INVITE_KEY] = code
            st.session_state[MODE_KEY] = SIGNUP
            del st.query_params["invitation"]
        # Sélecteur mémorisé (clé de session) : l'accueil peut pré-sélectionner « Créer un compte ».
        mode = LOGIN
        st.session_state.setdefault(MODE_KEY, LOGIN)
        if allow_signup:
            mode = (
                st.segmented_control(
                    "Mode", [LOGIN, SIGNUP], key=MODE_KEY, label_visibility="collapsed", width="stretch"
                )
                or LOGIN
            )

        if mode == LOGIN:
            _login_form()
        else:
            _signup_form()


def _login_form() -> None:
    with st.form("login"):
        email = st.text_input("Email", autocomplete="email")
        password = st.text_input("Mot de passe", type="password", autocomplete="current-password")
        remember = st.checkbox("Rester connecté", value=True, help="Sur cet appareil, pendant 30 jours.")
        if st.form_submit_button("Se connecter", type="primary", use_container_width=True):
            user = authenticate(email, password)
            if user:
                sign_in(user, remember=remember)
                go("dashboard")
            else:
                st.error("Email ou mot de passe incorrect.")


def _signup_form() -> None:
    need_code = invitation_required()
    code = st.session_state.get(INVITE_KEY, "")
    invited = check_invitation(code) if code else None
    if need_code:
        if invited:
            st.success(
                "Invitation valide : crée ton compte avec l'adresse invitée.", icon=":material/verified:"
            )
        elif code:
            st.warning(
                "Ce lien d'invitation n'est plus valide (expiré, annulé ou déjà utilisé).",
                icon=":material/link_off:",
            )
        else:
            st.info(
                "L'inscription se fait **sur invitation**. Saisis le code reçu par e-mail, "
                "ou demande une invitation ci-dessous.",
                icon=":material/mail_lock:",
            )
    email0, name0 = invited or ("", "")
    with st.form("signup"):
        full_name = st.text_input("Nom complet", value=name0 or "", autocomplete="name")
        email = st.text_input("Email", value=email0, autocomplete="email")
        password = st.text_input(
            "Mot de passe", type="password", help="8 caractères minimum", autocomplete="new-password"
        )
        confirm = st.text_input("Confirmation", type="password", autocomplete="new-password")
        invitation = None
        if need_code:
            invitation = st.text_input(
                "Code d'invitation", value=code, help="Reçu par e-mail avec ton invitation."
            )
        if st.form_submit_button("Créer mon compte", type="primary", use_container_width=True):
            if password != confirm:
                st.error("Les mots de passe ne correspondent pas.")
            else:
                try:
                    sign_in(create_user(email, password, full_name, invitation=invitation))
                except AuthError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.pop(INVITE_KEY, None)
                    go("profile")
    if need_code and not invited:
        with st.expander("Pas encore d'invitation ? Demande un accès", icon=":material/mail:"):
            request_form("login")
