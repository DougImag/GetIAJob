"""Point d'entrée Streamlit : `getiajob web` (ou `streamlit run src/getiajob/web/app.py`)."""

from __future__ import annotations

import streamlit as st

from getiajob.db import ReadOnlyError
from getiajob.services.mail_alerts import mailbox_configured, pending_mail_types
from getiajob.web import layout, state
from getiajob.web.views import (
    account,
    admin_monitoring,
    admin_quality,
    admin_settings,
    admin_sources,
    admin_users,
    applications,
    dashboard,
    home,
    login,
    mail_types,
    profile,
    results,
    search,
)

st.set_page_config(
    page_title="GetIAJob",
    page_icon=":material/work:",
    layout="wide",
    initial_sidebar_state="expanded",
)
layout.inject_css()

try:
    state.get_runner()
except Exception as exc:  # base injoignable : message clair plutôt qu'une trace
    st.error(
        "Impossible d'initialiser l'application : la base de données est-elle démarrée ? "
        "(`docker compose up -d db`)"
    )
    st.caption(str(exc))
    st.stop()

state.restore_session()
state.sync_oidc_user()
user = state.sync_admin_flag()
state.apply_read_only()

# clé: (vue, titre, icône, url)
PAGE_SPECS = {
    "home": (home.render, "Accueil", ":material/home:", "accueil"),
    "login": (login.render, "Connexion", ":material/login:", "connexion"),
    "dashboard": (dashboard.render, "Tableau de bord", ":material/space_dashboard:", "tableau-de-bord"),
    "profile": (profile.render, "Mon profil", ":material/account_circle:", "profil"),
    "search": (search.render, "Nouvelle recherche", ":material/manage_search:", "recherche"),
    "results": (results.render, "Mes recherches", ":material/work_history:", "resultats"),
    "applications": (
        applications.render,
        "Mes candidatures",
        ":material/assignment_turned_in:",
        "candidatures",
    ),
    "mail_alerts": (
        mail_types.render,
        "Mes alertes e-mail",
        ":material/forward_to_inbox:",
        "alertes-email",
    ),
    "account": (account.render, "Mon compte", ":material/manage_accounts:", "compte"),
    "admin_users": (admin_users.render, "Utilisateurs", ":material/group:", "admin-utilisateurs"),
    "admin_sources": (
        admin_sources.render,
        "Sources d'annonces",
        ":material/travel_explore:",
        "admin-sources",
    ),
    "admin_monitoring": (
        admin_monitoring.render,
        "Supervision",
        ":material/monitor_heart:",
        "admin-supervision",
    ),
    "admin_quality": (admin_quality.render, "Qualité & IA", ":material/insights:", "admin-qualite"),
    "admin_settings": (admin_settings.render, "Paramètres", ":material/tune:", "admin-parametres"),
}
if user and mailbox_configured() and (n_pending := pending_mail_types(user.id)):
    # Nouveaux types de mail à valider : signalés dans le menu
    view, title, icon, url = PAGE_SPECS["mail_alerts"]
    PAGE_SPECS["mail_alerts"] = (view, f"{title} ({n_pending})", icon, url)
default_page = "dashboard" if user else "home"
pages = {
    key: st.Page(view, title=title, icon=icon, url_path=url, default=key == default_page)
    for key, (view, title, icon, url) in PAGE_SPECS.items()
}

if user:
    # « Accueil » et « Mon compte » restent enregistrées mais sont masquées du menu (CSS) :
    # on y accède par le logo et par le menu utilisateur du header.
    navigation = {
        "": [pages["home"], pages["account"]],
        "Mes ressources": [pages["dashboard"], pages["profile"]],
        "Recherche": [pages["search"], pages["results"], pages["applications"]],
    }
    if mailbox_configured():
        navigation["Recherche"].append(pages["mail_alerts"])
    if user.is_admin:
        navigation["Admin"] = [
            pages["admin_monitoring"],
            pages["admin_quality"],
            pages["admin_users"],
            pages["admin_sources"],
            pages["admin_settings"],
        ]
else:
    navigation = {"": [pages["home"], pages["login"]]}

state.register_pages(pages)
# Déconnecté : pas de menu latéral, on navigue par le header (logo → accueil, bouton de connexion)
# expanded=True : toutes les pages toujours visibles (pas de « View more » au-delà de 10 entrées)
current = st.navigation(navigation, position="sidebar" if user else "hidden", expanded=True)
state.pending_goto()
layout.header(current.title, user)
if state.impersonator() is not None:  # lecture seule ou mode édition
    layout.impersonation_banner(user, state.impersonator())
if user:
    layout.sidebar_footer()
    state.open_sidebar_after_login()
state.apply_cookie_ops()
try:
    current.run()
except ReadOnlyError as exc:  # garde-fou : toute écriture est refusée pendant une consultation
    st.warning(str(exc), icon=":material/visibility:")
