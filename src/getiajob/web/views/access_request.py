"""Formulaire public de demande d'accès (adhésion sur invitation) : accueil et page de connexion."""

from __future__ import annotations

import streamlit as st

from getiajob.services.access import MAX_MESSAGE, submit_request
from getiajob.services.users import AuthError

SENT_KEY = "_access_request_sent"  # adresse de la demande envoyée pendant cette session


def request_form(key: str) -> None:
    """Formulaire « Demander une invitation ». `key` distingue les instances (accueil / connexion)."""
    if sent := st.session_state.get(SENT_KEY):
        st.success(
            f"Demande envoyée pour **{sent}**. Tu recevras ton invitation par e-mail dès qu'un "
            "administrateur l'aura validée.",
            icon=":material/mark_email_read:",
        )
        return
    with st.form(f"access_request_{key}", border=False, enter_to_submit=False):
        c1, c2 = st.columns(2)
        full_name = c1.text_input("Nom complet *", autocomplete="name", key=f"ar_name_{key}")
        email = c2.text_input(
            "Email *",
            autocomplete="email",
            key=f"ar_email_{key}",
            help="L'invitation sera envoyée à cette adresse, qui sera celle de ton compte.",
        )
        message = st.text_area(
            "Ton projet (facultatif)",
            max_chars=MAX_MESSAGE,
            height=90,
            placeholder="Poste recherché, comment tu as connu GetIAJob…",
            key=f"ar_msg_{key}",
        )
        if st.form_submit_button(
            "Demander une invitation", type="primary", icon=":material/send:", use_container_width=True
        ):
            try:
                submit_request(full_name, email, message)
            except AuthError as exc:
                st.error(str(exc))
            else:
                st.session_state[SENT_KEY] = email.strip().lower()
                st.rerun()
    st.caption("Tes informations servent uniquement à traiter ta demande.")
