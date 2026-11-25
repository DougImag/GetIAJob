"""Page « Mes alertes e-mail » : transfert des alertes, import à la demande, types de mail, derniers mails."""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from getiajob.config import get_settings
from getiajob.services.mail_alerts import (
    IMPORT_COOLDOWN,
    STATUS_LABELS,
    TYPE_STATUS_LABELS,
    ImportThrottled,
    MailTypeRow,
    SyncReport,
    import_for_user,
    list_mail_types,
    mailbox_configured,
    next_import_at,
    rename_mail_type,
    set_mail_type_status,
    user_imports,
)
from getiajob.services.users import SessionUser
from getiajob.web.state import impersonator, is_read_only, require_user

STATUS_BADGES = {"pending": "orange", "enabled": "green", "ignored": "gray"}
STATUS_ICONS = {"pending": "new_releases", "enabled": "check_circle", "ignored": "block"}
MAIL_COLORS = {
    "imported": "green",
    "not_alert": "gray",
    "new_type": "orange",
    "ignored_type": "gray",
    "unknown_sender": "orange",
    "unverified": "red",
}
RESULT_KEY = "mail_import_result"


def _acting_admin(user: SessionUser) -> bool:
    """Import sans limite pour un administrateur, y compris quand il consulte un autre compte."""
    admin = impersonator()
    return bool(user.is_admin or (admin is not None and admin.is_admin))


def _run_import(user: SessionUser, admin: bool, after_enable: str | None = None) -> None:
    with st.spinner("Relève de la boîte et extraction des offres (jusqu'à ~20 s par nouveau mail)…"):
        try:
            report = import_for_user(user.id, admin)
        except ImportThrottled as exc:
            message = str(exc)
            if after_enable:
                message = (
                    f"{after_enable} est ajouté : ses offres seront importées au prochain import ou à ta "
                    f"prochaine recherche. {message}"
                )
            st.session_state[RESULT_KEY] = ("throttled", message)
            return
    st.session_state[RESULT_KEY] = ("done", report)


def _show_result(user: SessionUser) -> None:
    if (state := st.session_state.pop(RESULT_KEY, None)) is None:
        return
    kind, value = state
    if kind == "throttled":
        st.warning(value, icon=":material/timer:")
        return
    report: SyncReport = value
    if report.skipped:
        st.info("Une relève est déjà en cours (recherche ou tâche planifiée) : réessaie dans un instant.")
        return
    if report.error:
        st.error(f"Relève impossible : {report.error}", icon=":material/error:")
        return
    mine = [r for r in report.results if r.user_id == user.id]
    offers = sum(len(r.postings) for r in mine if r.status == "imported")
    new_types = sorted({r.type_label or r.mail_type for r in mine if r.status == "new_type"})
    if not mine:
        st.success("Aucun nouveau mail de ta part dans la boîte.", icon=":material/inbox:")
    else:
        st.success(
            f"{len(mine)} nouveau(x) mail(s) traité(s) · {offers} offre(s) importée(s).",
            icon=":material/move_to_inbox:",
        )
    if new_types:
        st.warning(
            f"Nouveau(x) type(s) de mail : {', '.join(new_types)}. "
            "Ajoute-les ci-dessous pour importer leurs offres.",
            icon=":material/new_releases:",
        )


def _import_bar(user: SessionUser, read_only: bool) -> None:
    admin = _acting_admin(user)
    available_at = next_import_at(user.id, admin)
    with st.container(border=True):
        c1, c2 = st.columns([3, 1.4], vertical_alignment="center")
        minutes = int(IMPORT_COOLDOWN.total_seconds() // 60)
        if admin:
            c1.markdown(
                "**Importer mes alertes maintenant**  \n"
                ":gray[La boîte est aussi relevée à chaque recherche. Administrateur : import sans limite.]"
            )
        else:
            c1.markdown(
                "**Importer mes alertes maintenant**  \n"
                ":gray[La boîte est aussi relevée à chaque recherche. "
                f"Un import toutes les {minutes} minutes.]"
            )
        wait = ""
        if available_at is not None:
            seconds = max(0, int((available_at - datetime.now(UTC)).total_seconds()))
            wait = f"Disponible dans {seconds // 60} min {seconds % 60:02d} s"
            c1.caption(f":material/timer: {wait} (à {available_at.astimezone():%H:%M}).")
        if c2.button(
            "Importer",
            icon=":material/move_to_inbox:",
            type="primary",
            disabled=read_only or available_at is not None,
            help=wait or None,
            width="stretch",
            key="mail_import_now",
        ):
            _run_import(user, admin)
            st.rerun()


def _type_card(user: SessionUser, t: MailTypeRow, read_only: bool) -> None:
    with st.container(border=True, key=f"gj_mailtype_{t.id}"):
        c1, c2 = st.columns([3, 2], vertical_alignment="center")
        badge = (
            f":{STATUS_BADGES[t.status]}-badge[:material/{STATUS_ICONS[t.status]}: "
            f"{TYPE_STATUS_LABELS[t.status]}]"
        )
        builtin = "  :violet-badge[Reconnu d'office]" if t.builtin else ""
        seen = f" · dernier le {t.last_seen_at.astimezone():%d/%m/%Y}" if t.last_seen_at else ""
        c1.markdown(
            f"**{t.label}** {badge}{builtin}  \n"
            f":gray[{t.domain} · {t.n_mails} mail(s) reçu(s) · {t.n_offers} offre(s) importée(s){seen}]"
        )
        if t.sample_subject:
            c1.caption(
                f":material/mail: « {t.sample_subject} »"
                + (f" — {t.sample_sender}" if t.sample_sender else "")
            )
        with c2.container(horizontal=True, horizontal_alignment="right", gap="small"):
            if t.status != "enabled":
                label = "Ajouter" if t.status == "pending" else "Réactiver"
                if st.button(
                    label, icon=":material/add_task:", type="primary", key=f"mt_on_{t.id}", disabled=read_only
                ):
                    set_mail_type_status(user.id, t.id, "enabled")
                    # Import aussitôt si la limite le permet (sinon : prochain import ou recherche)
                    _run_import(user, _acting_admin(user), after_enable=t.label)
                    st.rerun()
            if t.status != "ignored":
                if st.button("Ignorer", icon=":material/block:", key=f"mt_off_{t.id}", disabled=read_only):
                    set_mail_type_status(user.id, t.id, "ignored")
                    st.rerun()
            with st.popover("", icon=":material/edit:", help="Renommer", disabled=read_only):
                name = st.text_input("Nom affiché", value=t.label, key=f"mt_name_{t.id}")
                if st.button("Enregistrer", key=f"mt_save_{t.id}", type="primary"):
                    rename_mail_type(user.id, t.id, name)
                    st.rerun()


def _history(user: SessionUser) -> None:
    rows = user_imports(user.id)
    if not rows:
        return
    st.markdown("#### Derniers mails reçus")
    with st.container(border=True):
        for r in rows:
            when = f"{r.received_at.astimezone():%d/%m/%Y %H:%M}" if r.received_at else "—"
            kind = f" · {r.type_label or r.mail_type}" if (r.type_label or r.mail_type) else ""
            offers = f" · **{r.n_offers} offre(s)**" if r.n_offers else ""
            st.markdown(
                f":{MAIL_COLORS.get(r.status, 'gray')}-badge[{STATUS_LABELS.get(r.status, r.status)}] "
                f"{r.subject or '(sans objet)'}  \n:gray[{when}{kind}]{offers}"
            )


def render() -> None:
    user = require_user()
    st.markdown("## Mes alertes e-mail")
    if not mailbox_configured():
        st.info(
            "La réception des alertes e-mail n'est pas configurée sur ce serveur.",
            icon=":material/mail_lock:",
        )
        return
    settings = get_settings()
    st.caption(
        f"Transfère tes alertes d'offres (APEC, Free-Work, Indeed, Welcome to the Jungle…) à "
        f"**{settings.imap_user}** depuis **{user.email}**, à la main ou par une règle de transfert "
        "automatique. Chaque site émetteur est un **type de mail** : l'APEC est reconnue d'office ; un "
        "nouveau site apparaît ici « à valider » dès son premier mail. **Ajoute-le** pour que l'IA en "
        "extraie les offres (y compris celles des mails déjà reçus), ou ignore-le. Les offres importées ne "
        "sont visibles que par toi et entrent dans tes recherches via la source « Alertes e-mail »."
    )
    read_only = is_read_only()
    _import_bar(user, read_only)
    _show_result(user)

    st.markdown("#### Mes types de mail")
    types = list_mail_types(user.id)
    if not types:
        st.info(
            "Aucune alerte reçue pour l'instant. Transfère une alerte puis lance un import : son type "
            "apparaîtra ici.",
            icon=":material/forward_to_inbox:",
        )
    else:
        pending = sum(t.status == "pending" for t in types)
        if pending:
            st.warning(
                f"{pending} nouveau(x) type(s) de mail reçu(s) : ajoute-les pour importer leurs offres.",
                icon=":material/new_releases:",
            )
        for t in types:
            _type_card(user, t, read_only)
    _history(user)
