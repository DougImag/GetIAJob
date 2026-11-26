"""Admin › Utilisateurs : comptes (rôle administrateur, suppression) et demandes d'accès / invitations."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from urllib.parse import urlsplit

import streamlit as st

from getiajob.services.access import (
    STATUS_LABELS,
    Invitation,
    RequestRow,
    invite,
    list_requests,
    reject,
    requests_open,
    revoke,
)
from getiajob.services.admin import UserRow, delete_user, list_users, set_admin, set_token_limit
from getiajob.services.users import AuthError, SessionUser
from getiajob.web.state import go, require_admin, start_impersonation
from getiajob.web.views.admin_format import tokens_label
from getiajob.web.views.cv import ico

CSS = """
<style>
  .gj-au-stats { display: flex; flex-wrap: wrap; gap: .5rem; margin: .2rem 0 1rem; }
  .gj-au-stat { padding: .35rem .8rem; border-radius: 999px; font-size: .82rem; color: var(--gj-muted);
    background: var(--gj-surface-2); border: 1px solid var(--gj-line); }
  .gj-au-stat b { color: var(--gj-head); font-weight: 800; margin-right: .2rem; }
  [class*="st-key-gj_user_"] { border-radius: 16px !important; }
  .gj-au { display: flex; gap: .9rem; align-items: center; min-width: 0; }
  .gj-au-av { flex-shrink: 0; width: 2.7rem; height: 2.7rem; border-radius: 50%; display: grid;
    place-items: center; color: #fff; font-weight: 800;
    background: linear-gradient(135deg, var(--gj-primary), var(--gj-primary-2)); }
  .gj-au-av.admin { background: linear-gradient(135deg, #f59e0b, #ea580c); }
  .gj-au-main { min-width: 0; }
  .gj-au-name { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; font-weight: 700;
    color: var(--gj-head); }
  .gj-au-tag { font-size: .7rem; font-weight: 700; padding: .08rem .5rem; border-radius: 999px;
    background: rgba(99, 102, 241, .12); color: var(--gj-primary); }
  .gj-au-tag.admin { background: rgba(245, 158, 11, .16); color: #b45309; }
  .gj-au-tag.me { background: rgba(34, 197, 94, .14); color: #15803d; }
  .gj-au-mail { font-size: .85rem; color: var(--gj-muted); overflow: hidden; text-overflow: ellipsis; }
  .gj-au-meta { display: flex; flex-wrap: wrap; gap: .2rem .9rem; margin-top: .3rem; font-size: .78rem;
    color: var(--gj-muted); }
  .gj-au-meta span { display: inline-flex; align-items: center; gap: .25rem; }
  .gj-au-meta span.ai-warn { color: #c2410c; font-weight: 600; }
  .gj-au-meta span.ai-full { color: #dc2626; font-weight: 700; }
  .gj-au-tag.pending { background: rgba(245, 158, 11, .16); color: #b45309; }
  .gj-au-tag.invited { background: rgba(99, 102, 241, .14); color: var(--gj-primary); }
  .gj-au-tag.used { background: rgba(34, 197, 94, .14); color: #15803d; }
  .gj-au-tag.rejected, .gj-au-tag.revoked, .gj-au-tag.expired {
    background: rgba(107, 114, 128, .16); color: var(--gj-muted); }
  .gj-ar-msg { margin: .55rem 0 0; padding: .45rem .75rem; border-left: 3px solid var(--gj-line);
    color: var(--gj-text); font-size: .88rem; white-space: pre-wrap; }
</style>
"""


def _ago(when: datetime | None) -> str:
    if when is None:
        return "jamais"
    days = (datetime.now(UTC) - when).days
    if days <= 0:
        return "aujourd'hui"
    if days == 1:
        return "hier"
    return f"il y a {days} j"


def _initials(row: UserRow) -> str:
    parts = [w for w in row.display_name.replace(".", " ").replace("-", " ").split() if w[:1].isalnum()]
    return ("".join(w[0] for w in parts[:2]) or "?").upper()


def _identity(row: UserRow, me: int) -> str:
    tags = []
    if row.is_admin:
        tags.append('<span class="gj-au-tag admin">Admin</span>')
    if row.id == me:
        tags.append('<span class="gj-au-tag me">Moi</span>')
    tags.append(f'<span class="gj-au-tag">{"Mot de passe" if row.provider == "local" else "SSO"}</span>')
    meta = (
        f"<span>{ico('calendar', 13)}Inscrit le {row.created_at:%d/%m/%Y}</span>"
        f"<span>{ico('clock', 13)}Dernière connexion {_ago(row.last_login_at)}</span>"
        f"<span>{ico('file', 13)}{row.n_cvs} CV</span>"
        f"<span>{ico('search', 13)}{row.n_searches} recherche(s)</span>"
        f"<span>{ico('heart', 13)}{row.n_feedback} avis</span>"
        f"{_ai_meta(row)}"
    )
    return (
        f'<div class="gj-au"><div class="gj-au-av{" admin" if row.is_admin else ""}">{escape(_initials(row))}'
        f'</div><div class="gj-au-main"><div class="gj-au-name">{escape(row.display_name)}{"".join(tags)}'
        f'</div><div class="gj-au-mail">{escape(row.email)}</div>'
        f'<div class="gj-au-meta">{meta}</div></div></div>'
    )


def _ai_meta(row: UserRow) -> str:
    """Consommation IA du mois, rapportée à la limite de l'utilisateur s'il en a une."""
    used = tokens_label(row.month_tokens)
    if not row.token_limit:
        return f"<span>{ico('sparkles', 13)}IA : {used} tokens ce mois</span>"
    pct = round(100 * row.month_tokens / row.token_limit)
    tone = ' class="ai-full"' if pct >= 100 else ' class="ai-warn"' if pct >= 80 else ""
    return (
        f"<span{tone}>{ico('sparkles', 13)}IA : {used} / {tokens_label(row.token_limit)} tokens "
        f"ce mois ({pct} %)</span>"
    )


def _token_limit_popover(row: UserRow, admin: SessionUser) -> None:
    # Libellé court, la limite figurant déjà dans « IA : … / … tokens ce mois » : boutons sur une ligne
    with st.popover("Limite IA", icon=":material/data_usage:", help="Limite mensuelle de tokens de l'IA"):
        st.markdown(f"**Limite mensuelle de tokens** pour {escape(row.display_name)}")
        st.caption(
            f"Consommés depuis le 1er du mois : {tokens_label(row.month_tokens)} tokens (tous LLM, Ollama "
            "compris). Une fois la limite atteinte, ses analyses de CV, recherches et alertes e-mail sont "
            "bloquées jusqu'au mois suivant. S'ajoute à la limite globale d'Admin › Paramètres."
        )
        enabled = st.toggle("Limiter", value=bool(row.token_limit), key=f"tl-on-{row.id}")
        tokens = st.number_input(
            "Tokens par mois",
            min_value=1_000,
            step=100_000,
            value=row.token_limit or 500_000,
            disabled=not enabled,
            key=f"tl-n-{row.id}",
        )
        new = int(tokens) if enabled else None
        if st.button(
            "Enregistrer", type="primary", icon=":material/save:", key=f"tl-save-{row.id}",
            disabled=new == row.token_limit,
        ):  # fmt: skip
            message = (
                f"Limite de {row.display_name} : {tokens_label(new)} tokens par mois"
                if new
                else f"Limite de {row.display_name} retirée"
            )
            _run(lambda: set_token_limit(admin.id, row.id, new), message)


def _user_card(row: UserRow, admin: SessionUser) -> None:
    me = admin.id
    with st.container(border=True, key=f"gj_user_{row.id}"):
        c1, c2 = st.columns([1, 1], vertical_alignment="center")
        c1.markdown(_identity(row, me), unsafe_allow_html=True)
        if row.id == me:
            with c2.container(horizontal=True, horizontal_alignment="right", gap="small"):
                _token_limit_popover(row, admin)
            c2.caption("Ton propre rôle ne peut pas être modifié ici.")
            return
        with c2.container(horizontal=True, horizontal_alignment="right", gap="small"):
            if st.button(
                "Consulter",
                icon=":material/visibility:",
                key=f"view-{row.id}",
                help="Voir l'application comme cet utilisateur, en lecture seule",
            ):
                try:
                    start_impersonation(admin, row.id)
                except AuthError as exc:
                    st.error(str(exc))
                else:
                    go("dashboard")
            label = "Retirer admin" if row.is_admin else "Rendre admin"
            icon = ":material/remove_moderator:" if row.is_admin else ":material/add_moderator:"
            if st.button(label, icon=icon, key=f"adm-{row.id}"):
                _run(
                    lambda: set_admin(me, row.id, not row.is_admin), f"Rôle de {row.display_name} mis à jour"
                )
            with st.popover("", icon=":material/delete:", help="Supprimer le compte"):
                st.markdown(
                    f"Supprimer `{row.email}` et toutes ses données "
                    "(CV, fiche, recherches, avis) ? Cette action est définitive."
                )
                if st.button("Supprimer définitivement", type="primary", key=f"del-{row.id}"):
                    _run(lambda: delete_user(me, row.id), f"Compte `{row.email}` supprimé")
            _token_limit_popover(row, admin)  # le plus à droite


LAST_INVITE_KEY = "_last_invitation"


def _request_identity(row: RequestRow) -> str:
    initials = ("".join(w[0] for w in row.display_name.split()[:2] if w[:1].isalnum()) or "?").upper()
    meta = [f"<span>{ico('calendar', 13)}Demande du {row.created_at:%d/%m/%Y}</span>"]
    if row.status in ("invited", "expired") and row.expires_at:
        meta.append(f"<span>{ico('clock', 13)}Invitation valable jusqu'au {row.expires_at:%d/%m/%Y}</span>")
    if row.used_at:
        meta.append(f"<span>{ico('clock', 13)}Compte créé le {row.used_at:%d/%m/%Y}</span>")
    elif row.decided_at and row.status in ("rejected", "revoked"):
        meta.append(f"<span>{ico('clock', 13)}Traitée le {row.decided_at:%d/%m/%Y}</span>")
    msg = f'<div class="gj-ar-msg">{escape(row.message)}</div>' if row.message else ""
    return (
        f'<div class="gj-au"><div class="gj-au-av">{escape(initials)}</div><div class="gj-au-main">'
        f'<div class="gj-au-name">{escape(row.display_name)}'
        f'<span class="gj-au-tag {row.status}">{STATUS_LABELS.get(row.status, row.status)}</span></div>'
        f'<div class="gj-au-mail">{escape(row.email)}</div>'
        f'<div class="gj-au-meta">{"".join(meta)}</div>{msg}</div></div>'
    )


def _app_url() -> str | None:
    """Adresse de l'application vue par le navigateur (ex. http://localhost:8501), pour le lien d'invitation
    quand PUBLIC_URL n'est pas renseigné."""
    try:
        parts = urlsplit(st.context.url or "")
    except Exception:  # noqa: BLE001 - hors navigateur (tests)
        return None
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None


def _invite(admin: SessionUser, email: str, full_name: str | None = None, request_id: int | None = None):
    try:
        st.session_state[LAST_INVITE_KEY] = invite(admin.id, email, full_name, request_id, _app_url())
    except AuthError as exc:
        st.error(str(exc))
        return
    st.rerun()


def _last_invitation() -> None:
    inv: Invitation | None = st.session_state.get(LAST_INVITE_KEY)
    if inv is None:
        return
    with st.container(border=True):
        if inv.mailed:
            st.success(f"Invitation envoyée à **{inv.email}**.", icon=":material/mark_email_read:")
        else:
            st.warning(
                f"Invitation créée pour **{inv.email}**, mais l'e-mail n'a pas pu être envoyé "
                f"({inv.mail_error}). Transmets-lui ce lien ou ce code toi-même :",
                icon=":material/warning:",
            )
        if inv.link:
            st.code(inv.link, language=None)
        st.caption(
            f"Code d'invitation (valable jusqu'au {inv.expires_at:%d/%m/%Y}, usage unique) : "
            f"`{inv.token}`" + ("" if inv.link else " — renseigne `PUBLIC_URL` pour envoyer un lien direct.")
        )
        if st.button("Masquer", key="hide_last_invite", icon=":material/close:"):
            st.session_state.pop(LAST_INVITE_KEY, None)
            st.rerun()


def _request_card(row: RequestRow, admin: SessionUser) -> None:
    with st.container(border=True, key=f"gj_user_req_{row.id}"):
        c1, c2 = st.columns([4, 3], vertical_alignment="center")
        c1.markdown(_request_identity(row), unsafe_allow_html=True)
        with c2.container(horizontal=True, horizontal_alignment="right", gap="small"):
            if row.status == "pending":
                if st.button("Accepter", type="primary", icon=":material/how_to_reg:", key=f"acc-{row.id}"):
                    _invite(admin, row.email, request_id=row.id)
                if st.button("Refuser", icon=":material/block:", key=f"rej-{row.id}"):
                    _run(lambda: reject(admin.id, row.id), f"Demande de {row.email} refusée")
            elif row.status in ("invited", "expired"):
                if st.button(
                    "Renvoyer",
                    icon=":material/forward_to_inbox:",
                    key=f"resend-{row.id}",
                    help="Nouveau code (l'ancien ne fonctionne plus), envoyé par e-mail",
                ):
                    _invite(admin, row.email, request_id=row.id)
                if st.button("Annuler", icon=":material/cancel:", key=f"rev-{row.id}"):
                    _run(lambda: revoke(admin.id, row.id), f"Invitation de {row.email} annulée")
            elif row.status in ("rejected", "revoked"):
                if st.button("Inviter quand même", icon=":material/how_to_reg:", key=f"reinv-{row.id}"):
                    _invite(admin, row.email, request_id=row.id)


def _requests_tab(admin: SessionUser, rows: list[RequestRow]) -> None:
    if not requests_open():
        st.info(
            "L'adhésion sur invitation est désactivée (`INVITE_ONLY=false` ou `ALLOW_SIGNUP=false`) : "
            "le formulaire de demande n'est pas proposé aux visiteurs.",
            icon=":material/info:",
        )
    _last_invitation()
    with st.expander("Inviter une adresse", icon=":material/person_add:"):
        with st.form("invite_direct", border=False, clear_on_submit=True):
            c1, c2 = st.columns(2)
            email = c1.text_input("Email *")
            name = c2.text_input("Nom (facultatif)")
            if st.form_submit_button("Envoyer l'invitation", type="primary", icon=":material/send:"):
                _invite(admin, email, name or None)

    groups = {
        "À traiter": [r for r in rows if r.status == "pending"],
        "Invitations": [r for r in rows if r.status in ("invited", "expired")],
        "Historique": [r for r in rows if r.status in ("used", "rejected", "revoked")],
    }
    labels = {f"{k} ({len(v)})": k for k, v in groups.items()}
    choice = st.segmented_control(
        "Filtre", list(labels), default=next(iter(labels)), label_visibility="collapsed", key="ar_filter"
    )
    shown = groups[labels.get(choice, "À traiter")]
    if not shown:
        st.info("Rien à afficher ici.", icon=":material/inbox:")
    for row in shown:
        _request_card(row, admin)


def _run(action, message: str) -> None:
    try:
        action()
    except AuthError as exc:
        st.error(str(exc))
        return
    st.toast(message, icon=":material/check_circle:")
    st.rerun()


def render() -> None:
    user = require_admin()
    st.markdown("## Utilisateurs")
    st.html(CSS)
    requests = list_requests()
    pending = sum(r.status == "pending" for r in requests)
    accounts, access = st.tabs(
        ["Comptes", f"Demandes d'accès ({pending})" if pending else "Demandes d'accès"]
    )
    with accounts:
        _accounts_tab(user)
    with access:
        _requests_tab(user, requests)


def _accounts_tab(user: SessionUser) -> None:
    rows = list_users()

    stats = [
        (len(rows), "compte(s)"),
        (sum(r.is_admin for r in rows), "administrateur(s)"),
        (sum(r.n_cvs for r in rows), "CV"),
        (sum(r.n_searches for r in rows), "recherche(s)"),
    ]
    chips = "".join(f'<span class="gj-au-stat"><b>{n}</b>{label}</span>' for n, label in stats)
    st.markdown(f'<div class="gj-au-stats">{chips}</div>', unsafe_allow_html=True)

    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    query = c1.text_input(
        "Rechercher", placeholder="Nom ou email…", label_visibility="collapsed", icon=":material/search:"
    )
    role = c2.segmented_control(
        "Rôle", ["Tous", "Admins", "Utilisateurs"], default="Tous", label_visibility="collapsed"
    )

    q = query.strip().lower()
    shown = [
        r
        for r in rows
        if (not q or q in r.email.lower() or q in (r.full_name or "").lower())
        and (role in (None, "Tous") or (role == "Admins") == r.is_admin)
    ]
    if not shown:
        st.info("Aucun compte ne correspond.", icon=":material/person_search:")
    for row in shown:
        _user_card(row, user)
