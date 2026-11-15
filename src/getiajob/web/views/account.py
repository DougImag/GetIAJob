"""Mon compte : nom affiché, mot de passe, déconnexion."""

from __future__ import annotations

import streamlit as st

from getiajob.services.users import AuthError, change_password, update_account
from getiajob.web.state import refresh_user, require_user, sign_out


def render() -> None:
    user = require_user()
    st.markdown("## Mon compte")

    with st.container(border=True):
        st.markdown(f"**Email** : {user.email}")
        st.caption("Connexion par " + ("mot de passe" if user.provider == "local" else "SSO"))
        with st.form("account"):
            full_name = st.text_input("Nom affiché", value=user.full_name or "")
            if st.form_submit_button("Enregistrer"):
                refresh_user(update_account(user.id, full_name))
                st.toast("Compte mis à jour", icon=":material/check_circle:")
                st.rerun()

    if user.provider == "local":
        with st.container(border=True), st.form("password", clear_on_submit=True):
            st.markdown("**Changer de mot de passe**")
            current = st.text_input("Mot de passe actuel", type="password")
            new = st.text_input("Nouveau mot de passe", type="password", help="8 caractères minimum")
            confirm = st.text_input("Confirmation", type="password")
            if st.form_submit_button("Modifier"):
                if new != confirm:
                    st.error("Les mots de passe ne correspondent pas.")
                else:
                    try:
                        change_password(user.id, current, new)
                        st.success("Mot de passe modifié.")
                    except AuthError as exc:
                        st.error(str(exc))

    if st.button("Se déconnecter", icon=":material/logout:"):
        sign_out()
