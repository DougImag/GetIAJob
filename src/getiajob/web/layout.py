"""Mise en page commune : header, menu latéral, styles, petits composants."""

from __future__ import annotations

import json
from html import escape

import streamlit as st
import streamlit.components.v1 as components

from getiajob.config import get_settings
from getiajob.scraping.base import ALL_SOURCES, SOURCE_CHOICE_LABELS, configured_sources
from getiajob.services.users import SessionUser
from getiajob.web.state import go, impersonator, is_write_mode, set_write_mode, sign_out

REMOTE_LABELS = {"on-site": "Sur site", "hybrid": "Hybride", "remote": "Télétravail"}
EXPERIENCE_LABELS = {
    "internship": "Stage",
    "entry": "Débutant",
    "associate": "Junior / associé",
    "mid-senior": "Confirmé / senior",
    "director": "Directeur",
    "executive": "Dirigeant",
}
CONTRACT_OPTIONS = ["CDI", "CDD", "Freelance", "Intérim", "Alternance", "Stage"]
# Fraîcheur des annonces : ancienneté maximale (jours) → libellé
FRESHNESS_LABELS = {
    1: "24 h",
    3: "3 jours",
    7: "1 semaine",
    14: "2 semaines",
    21: "3 semaines",
    30: "1 mois",
}

SITE_NAME = "GetIAJob"

# Logo : icône Lucide « briefcase-business » (https://lucide.dev/icons/briefcase-business, licence ISC),
# en blanc, injectée en CSS devant le bouton « accueil » pour garder une navigation native Streamlit.
LOGO_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='white' "
    "stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M12 12h.01'/>"
    "<path d='M16 6V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2'/><path d='M22 13a18.15 18.15 0 0 1-20 0'/>"
    "<rect width='20' height='14' x='2' y='6' rx='2'/></svg>"
)
LOGO_URI = "data:image/svg+xml," + LOGO_SVG.replace("<", "%3C").replace(">", "%3E").replace("#", "%23")

CSS = """
<style>
  :root {
    --gj-primary: #4f46e5;
    --gj-primary-2: #7c3aed;
    --gj-header-h: 3.5rem;
    /* Surfaces et textes des composants maison (redéfinis par DARK_CSS en mode sombre) */
    --gj-surface: #fff; --gj-surface-2: #f8f7ff; --gj-line: #eceafd;
    --gj-text: #374151; --gj-muted: #6b7280; --gj-head: #1e1b4b;
  }
  .st-key-gj_impersonation { background: rgba(245, 158, 11, .12);
    border-color: rgba(245, 158, 11, .6) !important; }
  .st-key-gj_impersonation:has(.gj-write-mode) {
    background: rgba(220, 38, 38, .10); border-color: rgba(220, 38, 38, .6) !important; }
  /* Bascule lecture seule / mode édition (consultation d'un compte) : pastille dans le header */
  .st-key-gj_write_toggle { padding: .2rem .75rem .2rem .55rem; border-radius: 999px;
    background: rgba(255, 255, 255, .16); border: 1px solid rgba(255, 255, 255, .35);
    transition: background .2s ease, border-color .2s ease; }
  .st-key-gj_write_toggle:has(input:checked) { background: #dc2626; border-color: #fecaca; }
  .st-key-gj_write_toggle label { gap: .45rem; margin: 0; }
  .st-key-gj_write_toggle label p { color: #fff !important; font-weight: 600; font-size: .82rem;
    white-space: nowrap; }
  .st-key-gj_write_toggle label > span + div { background: rgba(255, 255, 255, .35) !important; }
  .st-key-gj_write_toggle label:has(input:checked) > span + div {
    background: rgba(255, 255, 255, .45) !important; }
  .st-key-gj_write_toggle [data-testid="stTooltipIcon"] { display: none; }
  /* Bandeau natif de Streamlit : invisible, seul le bouton de réouverture du menu reste cliquable */
  header[data-testid="stHeader"] {
    background: transparent; height: 0; min-height: 0; pointer-events: none;
    top: var(--gj-header-h); z-index: 1000;
  }
  header[data-testid="stHeader"] button { pointer-events: auto; }
  /* La barre d'outils native contient le bouton de réouverture du menu latéral : on ne masque
     que ses autres éléments (menu ⋮, actions, déploiement). */
  [data-testid="stDecoration"], [data-testid="stMainMenu"], [data-testid="stToolbarActions"],
  [data-testid="stAppDeployButton"] { display: none; }
  [data-testid="stExpandSidebarButton"] {
    margin: 2.25rem 0 0 .25rem; background: var(--background-color, #fff);
    border: 1px solid rgba(49, 51, 63, .2); border-radius: .5rem;
  }
  .block-container { padding-top: calc(var(--gj-header-h) + 1.5rem); max-width: 1200px; }
  [data-testid="stSidebarContent"] { padding-top: var(--gj-header-h); }

  /* Header fixe, sur toute la largeur de la fenêtre (au-dessus du menu latéral) */
  .st-key-gj_header {
    position: fixed; top: 0; left: 0; right: 0; height: var(--gj-header-h); z-index: 1000010;
    padding: 0 1rem; gap: .5rem;
    background: linear-gradient(120deg, var(--gj-primary), var(--gj-primary-2));
    box-shadow: 0 4px 18px rgba(79, 70, 229, .25);
  }
  .st-key-gj_brand { gap: .35rem; flex-wrap: nowrap; }
  .st-key-gj_header button {
    color: #fff; background: transparent; border-color: transparent; min-height: 2.25rem;
  }
  .st-key-gj_header button:hover { background: rgba(255, 255, 255, .15); color: #fff; }
  .st-key-gj_home button { padding: .25rem .5rem; gap: .55rem; }
  .st-key-gj_home button::before {
    content: ""; width: 1.5rem; height: 1.5rem; flex-shrink: 0;
    background: url("__LOGO__") center / contain no-repeat;
  }
  .st-key-gj_home button p { font-size: 1.2rem; font-weight: 700; letter-spacing: .2px; }
  .st-key-gj_header .gj-title {
    color: #fff; font-weight: 500; font-size: 1.05rem; white-space: nowrap; opacity: .9;
    margin: 0 0 0 .4rem !important; padding: 0 0 0 .9rem; line-height: 1.5rem;
    border-left: 1px solid rgba(255, 255, 255, .4);
  }
  .st-key-gj_brand [data-testid="stMarkdownContainer"] { margin: 0 !important; }
  .gj-email { font-size: .85rem; opacity: .65; }
  .st-key-gj_header .st-key-gj_login button {
    background: #fff; color: var(--gj-primary); border-color: #fff; font-weight: 600;
  }
  .st-key-gj_header .st-key-gj_login button:hover {
    background: rgba(255, 255, 255, .88); color: var(--gj-primary);
  }
  /* Page d'accueil : pas de menu latéral (classe ajoutée par la vue) */
  body:has(.gj-no-sidebar) section[data-testid="stSidebar"],
  body:has(.gj-no-sidebar) [data-testid="stExpandSidebarButton"] { display: none; }
  .st-key-gj_header [data-testid="stPopover"] button { border-color: rgba(255, 255, 255, .35); }
  /* Bouton sombre / clair : cercle plein, sans contour visible */
  .st-key-gj_header .st-key-gj_theme button {
    width: 2.25rem; height: 2.25rem; min-height: 2.25rem; padding: 0; border-radius: 50%;
    border: none; background: rgba(255, 255, 255, .16); display: grid; place-items: center;
  }
  .st-key-gj_header .st-key-gj_theme button:hover { background: rgba(255, 255, 255, .28); }
  .st-key-gj_header .st-key-gj_theme button:focus:not(:active) { border: none; box-shadow: none; }

  /* Onglets (st.tabs) en « pastilles » bien visibles ; l'onglet actif est rempli */
  [data-testid="stTabs"] [role="tablist"] {
    gap: .5rem; flex-wrap: wrap; padding: .35rem 0 .7rem; border: none; box-shadow: none;
  }
  [data-testid="stTabs"] [role="tablist"]::before,
  [data-testid="stTabs"] [role="tablist"]::after { display: none; }
  [data-testid="stTabs"] [role="tab"] {
    height: auto; padding: .45rem .95rem; border-radius: 999px; cursor: pointer;
    background: #f3f2ff; border: 1px solid #e0defb; color: #3730a3;
    transition: background .15s ease, border-color .15s ease, box-shadow .15s ease;
  }
  [data-testid="stTabs"] [role="tab"]::before,
  [data-testid="stTabs"] [role="tab"]::after,
  [data-testid="stTabs"] .react-aria-SelectionIndicator { display: none; }
  [data-testid="stTabs"] [role="tab"] p { font-size: .92rem; font-weight: 600; color: inherit; }
  [data-testid="stTabs"] [role="tab"] [data-testid="stIconMaterial"] { color: inherit; }
  [data-testid="stTabs"] [role="tab"]:hover { background: #e9e7ff; border-color: #c7c2f7; }
  [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    background: linear-gradient(120deg, var(--gj-primary), var(--gj-primary-2));
    border-color: transparent; color: #fff; box-shadow: 0 6px 16px -6px rgba(79, 70, 229, .6);
  }

  /* Liens masqués du menu latéral : accessibles via le logo et le menu utilisateur */
  [data-testid="stSidebarNavItems"] li:has(a[href$="/accueil"]),
  [data-testid="stSidebarNavItems"] li:has(a[href$="/compte"]) { display: none; }
  [data-testid="stSidebarHeader"] { height: 2.5rem; margin-bottom: 0; }

  .gj-hero { padding: 2.5rem 0 1.5rem; }
  .gj-hero h1 { font-size: clamp(2rem, 4vw, 3rem); line-height: 1.1; margin-bottom: .75rem; }
  .gj-hero h1 span {
    background: linear-gradient(120deg, var(--gj-primary), var(--gj-primary-2));
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .gj-hero p { font-size: 1.15rem; opacity: .8; max-width: 40rem; }

</style>
"""

# Surcharges du mode sombre pour les composants maison (accueil, cartes, onglets…)
DARK_CSS = """
<style>
  :root {
    --gj-surface: #161a24; --gj-surface-2: #1d2230; --gj-line: #2c3242;
    --gj-text: #e5e7eb; --gj-muted: #9ca3af; --gj-head: #e0e7ff;
  }
  [data-testid="stExpandSidebarButton"] { border-color: var(--gj-line); }
  [data-testid="stTabs"] [role="tab"] {
    background: var(--gj-surface-2); border-color: var(--gj-line); color: #c7d2fe;
  }
  [data-testid="stTabs"] [role="tab"]:hover { background: #252b3d; border-color: #4f46e5; }

  /* Accueil */
  .gj-h1, .gj-section-title, .gj-sh-title, .gj-kpi b, .gj-card-title a { color: var(--gj-head) !important; }
  .gj-lead, .gj-checks, .gj-summary, .gj-box li, .gj-skill-t { color: #d1d5db !important; }
  .gj-stat span, .gj-section-sub, .gj-feature p, .gj-step p, .gj-offer-meta, .gj-preview-bar b,
  .gj-sh-sub, .gj-kpi span, .gj-meta, .gj-ring small, .gj-rank { color: var(--gj-muted) !important; }
  .gj-ft, .gj-st, .gj-offer-title, .gj-ring b, .gj-score { color: var(--gj-text) !important; }
  .st-key-gj_hero {
    background:
      radial-gradient(circle at 12% 18%, rgba(124, 58, 237, .22), transparent 45%),
      radial-gradient(circle at 88% 80%, rgba(79, 70, 229, .22), transparent 50%),
      linear-gradient(180deg, #151829, #11131f) !important;
    border-color: rgba(129, 140, 248, .2) !important;
  }
  .gj-pill, .gj-feature, .gj-offer, .gj-kpi, .gj-step-num {
    background: var(--gj-surface) !important; border-color: var(--gj-line) !important;
  }
  .gj-step-num {
    box-shadow: 0 0 0 6px var(--background-color, #0e1117) !important; color: #a5b4fc !important;
  }
  .gj-pill { color: #a5b4fc !important; }
  .gj-preview { background: rgba(22, 26, 36, .92) !important; border-color: var(--gj-line) !important; }
  .gj-preview-bar i { background: #374151 !important; }
  .gj-tag, .gj-chip {
    background: #1e1b4b !important; color: #c7d2fe !important; border-color: #312e81 !important;
  }
  .gj-float { background: #4f46e5 !important; }
  .gj-score, .gj-ring {
    background: radial-gradient(var(--gj-surface) 58%, transparent 60%),
      conic-gradient(var(--c) calc(var(--v) * 1%), #2c3242 0) !important;
  }
  .gj-privacy {
    background: rgba(22, 163, 74, .1) !important; border-color: rgba(34, 197, 94, .3) !important;
  }
  .gj-privacy b { color: #bbf7d0 !important; } .gj-privacy p { color: #86efac !important; }
  .gj-mail {
    background: rgba(124, 58, 237, .1) !important; border-color: rgba(167, 139, 250, .3) !important;
  }
  .gj-mail-t, .gj-mail-step b { color: var(--gj-head) !important; }
  .gj-mail-head p, .gj-mail-note, .gj-mail-step { color: #d1d5db !important; }
  .gj-mail-step { background: var(--gj-surface) !important; border-color: var(--gj-line) !important; }
  .gj-mail-step i { color: #a5b4fc !important; }
  .gj-mail-addr { background: #1e1b4b !important; color: #c7d2fe !important; }
  .st-key-gj_invite { background: var(--gj-surface) !important; border-color: var(--gj-line) !important; }
  .gj-inv-t, .gj-inv-flow b, .gj-inv-form-t { color: var(--gj-head) !important; }
  .gj-inv-lead, .gj-inv-flow span { color: #d1d5db !important; }
  .gj-inv-badge { background: #1e1b4b !important; color: #c7d2fe !important; }

  .gj-stat b, .gj-hl { background-image: linear-gradient(120deg, #818cf8, #c084fc 60%, #f472b6) !important; }

  .gj-xp-co .co, .gj-xp-tag { color: #a5b4fc !important; } .gj-xp-tag.now { color: #86efac !important; }

  .gj-pf-head, .gj-xp-sub, .gj-pf-level, .gj-c-link { color: #a5b4fc !important; }
  .gj-chips2 span { color: #c7d2fe !important; } .gj-chips2.orange span { color: #fdba74 !important; }
  .gj-chips2.pink span { color: #f9a8d4 !important; } .gj-pf-targets span { color: #d8b4fe !important; }
  .gj-chips2.plain span { color: var(--gj-text) !important; }

  .gj-fr-chips span.todo { color: #fdba74 !important; }
  .gj-fr-chips span svg { color: #a5b4fc !important; }

  .gj-badge.done { color: #86efac !important; } .gj-badge.failed { color: #fca5a5 !important; }
  .gj-badge.cancelled { color: #d1d5db !important; }
  .gj-badge.running, .gj-badge.pending, .gj-db-t svg, .gj-dk-ring { color: #a5b4fc !important; }

  /* Mes recherches */
  .gj-status.ok, .gj-skill.ok { background: rgba(34, 197, 94, .16) !important; color: #86efac !important; }
  .gj-status.ko { background: rgba(239, 68, 68, .16) !important; color: #fca5a5 !important; }
  .gj-status.run { background: rgba(99, 102, 241, .2) !important; color: #c7d2fe !important; }
  .gj-skill.miss { background: rgba(249, 115, 22, .16) !important; color: #fdba74 !important; }
  .gj-skill.more { background: var(--gj-surface-2) !important; color: var(--gj-muted) !important; }
  .gj-verdict { background: color-mix(in srgb, var(--c) 18%, transparent) !important; }
  .gj-adj { background: rgba(167, 139, 250, .16) !important; color: #ddd6fe !important; }
  .gj-box.pros {
    background: rgba(22, 163, 74, .1) !important; border-color: rgba(34, 197, 94, .3) !important;
  }
  .gj-box.cons {
    background: rgba(249, 115, 22, .1) !important; border-color: rgba(249, 115, 22, .3) !important;
  }
  .gj-box.pros .gj-box-t { color: #86efac !important; } .gj-box.cons .gj-box-t { color: #fdba74 !important; }
  .gj-bars { color: #d1d5db !important; } .gj-bar { background: #2c3242 !important; }
</style>
"""


def inject_css() -> None:
    # Un bloc ne contenant que du <style> n'occupe aucune place dans la page
    st.html(CSS.replace("__LOGO__", LOGO_URI) + (DARK_CSS if is_dark() else ""))


def header(page_title: str, user: SessionUser | None) -> None:
    """Barre du haut : logo + nom du site (→ accueil), titre de la page, menu utilisateur."""
    with st.container(
        key="gj_header", horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"
    ):
        with st.container(key="gj_brand", horizontal=True, vertical_alignment="center", width="content"):
            with st.container(key="gj_home", width="content"):
                if st.button(SITE_NAME, help="Accueil", type="tertiary"):
                    go("home")
            st.markdown(f'<p class="gj-title">{escape(page_title)}</p>', unsafe_allow_html=True)

        with st.container(key="gj_actions", horizontal=True, vertical_alignment="center", width="content"):
            _write_mode_toggle(user)
            _theme_toggle()
            _user_menu(page_title, user)
    _apply_theme_switch()


WRITE_TOGGLE_KEY = "gj_write_mode"


def _write_mode_toggle(user: SessionUser | None) -> None:
    """Consultation d'un compte par un administrateur : lecture seule ↔ mode édition."""
    if user is None or impersonator() is None:
        return
    st.session_state[WRITE_TOGGLE_KEY] = is_write_mode()  # reflète l'état réel (droits relus en base)
    editing = is_write_mode()
    with st.container(key="gj_write_toggle", width="content"):
        st.toggle(
            ":material/edit: Mode édition" if editing else ":material/visibility: Lecture seule",
            key=WRITE_TOGGLE_KEY,
            on_change=lambda: set_write_mode(bool(st.session_state.get(WRITE_TOGGLE_KEY))),
        )


def _user_menu(page_title: str, user: SessionUser | None) -> None:
    if user:
        # Clé propre à chaque page : le menu se referme après une navigation
        with st.popover(
            user.display_name, icon=":material/account_circle:", key=f"gj_user_menu_{page_title}"
        ):
            st.html(f'<div class="gj-email">{escape(user.email)}</div>')
            if st.button("Mon compte", icon=":material/manage_accounts:", use_container_width=True):
                go("account")
            if st.button("Se déconnecter", icon=":material/logout:", use_container_width=True):
                sign_out()
    elif page_title != "Connexion":  # inutile sur la page de connexion elle-même
        with st.container(key="gj_login", width="content"):
            if st.button("Se connecter / S'inscrire", icon=":material/login:"):
                go("login")


# Chemins de toutes les pages (url_path de web/app.py) : Streamlit mémorise le thème par chemin
THEME_PATHS = (
    "",
    "accueil",
    "connexion",
    "tableau-de-bord",
    "profil",
    "recherche",
    "resultats",
    "candidatures",
    "compte",
    "admin-utilisateurs",
    "admin-sources",
    "admin-supervision",
    "admin-qualite",
)
THEME_SWITCH_KEY = "gj_theme_switch"


def is_dark() -> bool:
    return st.context.theme.type == "dark"


def _theme_toggle() -> None:
    dark = is_dark()
    with st.container(key="gj_theme", width="content"):
        if st.button(
            "",
            icon=":material/light_mode:" if dark else ":material/dark_mode:",
            help="Passer en affichage clair" if dark else "Passer en affichage sombre",
            type="tertiary",
        ):
            st.session_state[THEME_SWITCH_KEY] = "Light" if dark else "Dark"


def _apply_theme_switch() -> None:
    """Enregistre le thème choisi là où Streamlit le relit au chargement, puis recharge la page."""
    target = st.session_state.pop(THEME_SWITCH_KEY, None)
    if not target:
        return
    paths = json.dumps([f"/{p}" for p in THEME_PATHS])
    components.html(
        f"""<script>
        const w = window.parent, value = {json.dumps(json.dumps(target))};
        const keys = new Set({paths}.map(p => `stActiveTheme-${{p}}-v2`));
        keys.add(`stActiveTheme-${{w.location.pathname}}-v2`);
        for (let i = 0; i < w.localStorage.length; i++) {{
          const k = w.localStorage.key(i);
          if (k.startsWith("stActiveTheme-") && k.endsWith("-v2")) keys.add(k);
        }}
        keys.forEach(k => w.localStorage.setItem(k, value));
        w.location.reload();
        </script>""",
        height=0,
    )


def sidebar_footer() -> None:
    s = get_settings()
    with st.sidebar:
        # Modèles configurés (LLM_MODEL / EMBEDDING_MODEL), pas d'appel à Ollama à chaque affichage
        st.caption(  # « deux espaces + \n » = retour à la ligne en Markdown
            "Modèles open source"
            + (" via Ollama :  \n" if s.llm_provider == "ollama" else " :  \n")
            + f"`{s.llm_model}` (analyse et notes)  \n"
            f"`{s.embedding_model}` (similarité)"
        )


def freshness_input(label: str, value: int, key: str | None = None, help: str | None = None) -> int:
    """Curseur « fraîcheur des annonces » : ancienneté maximale d'une offre, en jours."""
    options = list(FRESHNESS_LABELS)
    closest = min(options, key=lambda d: abs(d - (value or 7)))
    return st.select_slider(
        label, options=options, value=closest, format_func=FRESHNESS_LABELS.get, key=key, help=help
    )


def sources_input(label: str, value: list[str] | None, key: str | None = None) -> list[str]:
    """Pastilles « Sources d'annonces » (seules les sources configurées sont proposées)."""
    available = configured_sources()
    default = [s for s in (value if value is not None else available) if s in available]
    missing = [SOURCE_CHOICE_LABELS[s] for s in ALL_SOURCES if s not in available]
    help_text = (
        "Sites interrogés à chaque recherche ; une même offre vue sur plusieurs sites n'apparaît qu'une fois."
    )
    if missing:
        help_text += (
            f" Non configuré(s) sur ce serveur : {', '.join(missing)} (clés d'API dans le fichier .env)."
        )
    picked = st.pills(
        label,
        options=available,
        default=default,
        selection_mode="multi",
        format_func=SOURCE_CHOICE_LABELS.get,
        key=key,
        help=help_text,
    )
    return list(picked or [])


def badges(items: list[str], color: str = "blue") -> str:
    """Liste de badges Markdown Streamlit (les crochets cassent la syntaxe : on les retire)."""
    clean = (i.replace("[", "(").replace("]", ")").strip() for i in items)
    return " ".join(f":{color}-badge[{i}]" for i in clean if i)


def score_color(score: float) -> str:
    return "green" if score >= 75 else "orange" if score >= 55 else "red"


def parse_list(value: str) -> list[str]:
    seen: list[str] = []
    for item in value.replace("\n", ",").split(","):
        item = item.strip()
        if item and item.lower() not in {s.lower() for s in seen}:
            seen.append(item)
    return seen


def impersonation_banner(user, admin) -> None:
    """Bandeau affiché quand un administrateur consulte un compte en lecture seule."""
    from getiajob.web.state import go_after_rerun, stop_impersonation

    with st.container(border=True, key="gj_impersonation"):
        c1, c2 = st.columns([5, 1.6], vertical_alignment="center")
        who = f"**{user.display_name}** ({user.email})"
        if is_write_mode():
            c1.markdown(
                '<span class="gj-write-mode"></span>:material/edit: **Mode édition** — '
                f"tu agis sur le compte de {who} comme si tu étais cet utilisateur : "
                "tes modifications sont réelles. Repasse en lecture seule avec le bouton du header.",
                unsafe_allow_html=True,
            )
        else:
            c1.markdown(
                f":material/visibility: **Lecture seule** — tu consultes le compte de {who}. "
                "Aucune modification n'est possible ; active la bascule **Lecture seule** du header "
                "pour agir à sa place."
            )
        if c2.button(
            "Revenir à mon compte", icon=":material/logout:", type="primary", use_container_width=True
        ):
            stop_impersonation()
            go_after_rerun("admin_users")
