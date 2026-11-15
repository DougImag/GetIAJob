"""Page d'accueil publique : présentation de GetIAJob."""

from __future__ import annotations

from html import escape

import streamlit as st

from getiajob.config import get_settings
from getiajob.services.access import requests_open
from getiajob.services.mail_alerts import mailbox_configured
from getiajob.web.state import current_user, go
from getiajob.web.views.access_request import request_form

# Icônes Lucide (https://lucide.dev, licence ISC) : contenu des <svg> 24×24 en trait.
ICONS = {
    "scan-text": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/>'
    '<path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M7 8h8"/>'
    '<path d="M7 12h10"/><path d="M7 16h6"/>',
    "radar": '<path d="M19.07 4.93A10 10 0 0 0 6.99 3.34"/><path d="M4 6h.01"/>'
    '<path d="M2.29 9.62A10 10 0 1 0 21.31 8.35"/><path d="M16.24 7.76A6 6 0 1 0 8.23 16.67"/>'
    '<path d="M12 18h.01"/><path d="M17.99 11.66A6 6 0 0 1 15.77 16.67"/><circle cx="12" cy="12" r="2"/>'
    '<path d="m13.41 10.59 5.66-5.66"/>',
    "sparkles": '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 '
    "0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 "
    '.964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/>'
    '<path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>',
    "thumbs-up": '<path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 '
    '17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/>',
    "shield-check": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 '
    '1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>'
    '<path d="m9 12 2 2 4-4"/>',
    "cpu": '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/>'
    '<path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/>'
    '<path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16.5 12"/>',
    "map-pin": '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 '
    '10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "ticket": '<path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 '
    '0-2-2H4a2 2 0 0 0-2 2Z"/><path d="M13 5v2"/><path d="M13 17v2"/><path d="M13 11v2"/>',
    "mail-forward": '<path d="M22 13V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v12c0 1.1.9 2 2 2h8"/>'
    '<path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/><path d="m15 17 5 0"/><path d="m18 14 3 3-3 3"/>',
}


def icon(name: str, size: int = 22) -> str:
    return (
        f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="currentColor" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f"{ICONS[name]}</svg>"
    )


FEATURES = [
    (
        "scan-text",
        "Analyse de ton CV",
        "Expériences, formations, compétences, langues : ton CV est découpé section par section "
        "pour en tirer ton vrai profil.",
    ),
    (
        "radar",
        "Collecte des offres",
        "Des requêtes ciblées sont générées depuis ton profil et ta fiche, puis les offres LinkedIn "
        "récentes sont récupérées et dédupliquées.",
    ),
    (
        "sparkles",
        "Tri intelligent",
        "Proximité sémantique puis évaluation par l'IA : une note sur 100, les points forts "
        "et les points de vigilance de chaque offre.",
    ),
    (
        "thumbs-up",
        "Il apprend de tes avis",
        "« J'aime », « Pas pour moi » et pourquoi : chaque avis affine les recherches suivantes, "
        "sans alourdir le calcul.",
    ),
]

SIGNUP_STEP = ("Crée ton compte", "Quelques secondes, tes données restent dans ta base.")
INVITE_STEP = ("Demande ton invitation", "Une fois validée, le code arrive par e-mail.")
STEPS = [
    SIGNUP_STEP,
    ("Charge ton CV", "PDF, Word, Markdown ou texte, analysé en arrière-plan."),
    ("Précise ta recherche", "Poste, lieux, fraîcheur des annonces, attentes."),
    ("Reçois ton top", "Les offres les plus pertinentes, notées et argumentées."),
]

# Adhésion sur invitation : étapes affichées à côté du formulaire de demande
INVITE_FLOW = [
    ("Fais ta demande", "Nom, email et, si tu veux, quelques mots sur ton projet."),
    ("Validation", "Un administrateur examine ta demande."),
    ("Invitation par e-mail", "Tu reçois un lien et un code à usage unique, valables {days} jours."),
    ("Crée ton compte", "Avec la même adresse : c'est elle qui ouvre ton accès."),
]

# Aperçu illustratif (données fictives) affiché dans le hero
PREVIEW = [
    (92, "#16a34a", "Machine Learning Engineer", "Start-up IA · Paris", ["PyTorch", "MLOps"]),
    (84, "#16a34a", "Data Scientist", "Scale-up FinTech · Lyon", ["Python", "SQL"]),
    (71, "#ea580c", "AI Engineer", "ESN · Télétravail", ["LLM", "RAG"]),
]

CSS = """
<style>
  .st-key-gj_hero {
    position: relative; overflow: hidden; border-radius: 24px; padding: 3rem 2.5rem;
    background:
      radial-gradient(circle at 12% 18%, rgba(124, 58, 237, .14), transparent 45%),
      radial-gradient(circle at 88% 80%, rgba(79, 70, 229, .16), transparent 50%),
      linear-gradient(180deg, #fbfaff, #f3f1ff);
    border: 1px solid rgba(79, 70, 229, .12);
  }
  .gj-pill {
    display: inline-flex; align-items: center; gap: .45rem; padding: .35rem .8rem; border-radius: 999px;
    background: #fff; border: 1px solid rgba(79, 70, 229, .2); color: var(--gj-primary);
    font-size: .82rem; font-weight: 600; box-shadow: 0 2px 10px rgba(79, 70, 229, .08);
  }
  .gj-pill .dot { width: .5rem; height: .5rem; border-radius: 50%; background: #22c55e;
    box-shadow: 0 0 0 3px rgba(34, 197, 94, .2); }
  .gj-h1 {
    font-size: clamp(2.2rem, 4.6vw, 3.6rem); line-height: 1.05; font-weight: 800;
    letter-spacing: -.02em; margin: 1.1rem 0 1rem; color: #1e1b4b;
  }
  .gj-h1 .gj-hl {
    background: linear-gradient(120deg, var(--gj-primary), var(--gj-primary-2) 60%, #db2777);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .gj-lead { font-size: 1.15rem; line-height: 1.6; color: #4b5563; max-width: 34rem; margin-bottom: 1.4rem; }
  .gj-checks { display: flex; flex-wrap: wrap; gap: .5rem 1.2rem; color: #374151; font-size: .92rem; }
  .gj-checks span { display: inline-flex; align-items: center; gap: .35rem; }
  .gj-checks svg { color: #16a34a; }

  /* Aperçu des résultats */
  .gj-preview {
    position: relative; background: rgba(255, 255, 255, .9); backdrop-filter: blur(6px);
    border: 1px solid rgba(79, 70, 229, .14); border-radius: 18px; padding: 1rem 1rem .6rem;
    box-shadow: 0 24px 60px -20px rgba(49, 46, 129, .35); transform: rotate(-1deg);
  }
  .gj-preview-bar { display: flex; align-items: center; gap: .35rem; margin-bottom: .8rem; }
  .gj-preview-bar i { width: .6rem; height: .6rem; border-radius: 50%; background: #e5e7eb; }
  .gj-preview-bar b { margin-left: .5rem; font-size: .8rem; color: #6b7280; font-weight: 600; }
  .gj-offer {
    display: flex; align-items: center; gap: .9rem; padding: .7rem; border-radius: 12px;
    border: 1px solid #f1f0fb; margin-bottom: .55rem; background: #fff;
  }
  .gj-score {
    --c: #16a34a; width: 3.1rem; height: 3.1rem; border-radius: 50%; flex-shrink: 0;
    display: grid; place-items: center; font-weight: 800; color: #111827; font-size: .95rem;
    background: radial-gradient(#fff 58%, transparent 60%),
      conic-gradient(var(--c) calc(var(--v) * 1%), #eef0f4 0);
  }
  .gj-offer-title { font-weight: 700; color: #111827; font-size: .95rem; }
  .gj-offer-meta { color: #6b7280; font-size: .8rem; margin: .1rem 0 .3rem; }
  .gj-tag {
    display: inline-block; font-size: .72rem; padding: .1rem .5rem; border-radius: 999px;
    background: #eef2ff; color: #4338ca; margin-right: .3rem;
  }
  .gj-float {
    position: absolute; right: -.6rem; bottom: -1rem; transform: rotate(2deg);
    display: inline-flex; align-items: center; gap: .4rem; padding: .45rem .75rem; border-radius: 12px;
    background: #1e1b4b; color: #fff; font-size: .8rem; font-weight: 600;
    box-shadow: 0 12px 30px -10px rgba(30, 27, 75, .6);
  }

  /* Chiffres clés */
  .gj-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin: 2.5rem 0 1rem; }
  .gj-stat { text-align: center; padding: 1rem .5rem; }
  .gj-stat b {
    display: block; font-size: 2rem; font-weight: 800; letter-spacing: -.02em;
    background: linear-gradient(120deg, var(--gj-primary), var(--gj-primary-2));
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
  .gj-stat span { color: #6b7280; font-size: .9rem; }

  /* Sections */
  .gj-section-title { text-align: center; margin: 3rem 0 .4rem; font-size: 2rem; font-weight: 800;
    letter-spacing: -.02em; color: #1e1b4b; }
  .gj-section-sub { text-align: center; color: #6b7280; margin-bottom: 1.8rem; }
  .gj-features { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; }
  .gj-feature {
    background: #fff; border: 1px solid #eceafd; border-radius: 18px; padding: 1.4rem 1.2rem;
    transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
  }
  .gj-feature:hover { transform: translateY(-4px); border-color: rgba(79, 70, 229, .35);
    box-shadow: 0 18px 40px -18px rgba(79, 70, 229, .45); }
  .gj-feature-icon {
    width: 2.8rem; height: 2.8rem; border-radius: 12px; display: grid; place-items: center; color: #fff;
    background: linear-gradient(135deg, var(--gj-primary), var(--gj-primary-2)); margin-bottom: 1rem;
    box-shadow: 0 8px 20px -8px rgba(79, 70, 229, .7);
  }
  .gj-ft { margin: 0 0 .4rem; font-size: 1.05rem; font-weight: 700; color: #111827; }
  .gj-feature p { margin: 0; color: #6b7280; font-size: .9rem; line-height: 1.5; }

  .gj-steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; position: relative; }
  .gj-steps::before {
    content: ""; position: absolute; top: 1.3rem; left: 12%; right: 12%; height: 2px;
    background: linear-gradient(
      90deg, rgba(79, 70, 229, .15), rgba(124, 58, 237, .5), rgba(79, 70, 229, .15)
    );
  }
  .gj-step { text-align: center; position: relative; padding: 0 .5rem; }
  .gj-step-num {
    width: 2.6rem; height: 2.6rem; margin: 0 auto .9rem; border-radius: 50%; display: grid;
    place-items: center; font-weight: 800; color: var(--gj-primary); background: #fff;
    border: 2px solid rgba(79, 70, 229, .35); box-shadow: 0 0 0 6px #fff;
  }
  .gj-st { margin: 0 0 .3rem; font-size: 1rem; font-weight: 700; color: #111827; }
  .gj-step p { margin: 0; color: #6b7280; font-size: .88rem; }

  .gj-privacy {
    display: flex; gap: 1.2rem; align-items: center; margin: 3rem 0 1rem; padding: 1.4rem 1.6rem;
    border-radius: 18px; background: #f0fdf4; border: 1px solid #bbf7d0;
  }
  .gj-privacy-icon { color: #16a34a; flex-shrink: 0; }
  .gj-privacy b { color: #14532d; }
  .gj-privacy p { margin: .2rem 0 0; color: #166534; font-size: .92rem; }

  /* Alertes e-mail transférées */
  .gj-mail {
    margin: 3rem 0 1rem; padding: 1.6rem; border-radius: 18px;
    background: #f5f3ff; border: 1px solid #ddd6fe;
  }
  .gj-mail-head { display: flex; gap: 1rem; align-items: center; margin-bottom: 1.1rem; }
  .gj-mail-icon {
    width: 2.8rem; height: 2.8rem; border-radius: 12px; display: grid; place-items: center; flex-shrink: 0;
    color: #fff; background: linear-gradient(135deg, var(--gj-primary), var(--gj-primary-2));
  }
  .gj-mail-t { font-size: 1.15rem; font-weight: 700; color: #1e1b4b; }
  .gj-mail-head p, .gj-mail-note { margin: .15rem 0 0; color: #4b5563; font-size: .92rem; }
  .gj-mail-steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: .8rem; }
  .gj-mail-step {
    background: #fff; border: 1px solid #ede9fe; border-radius: 14px; padding: .9rem 1rem;
    font-size: .9rem; color: #374151; line-height: 1.45;
  }
  .gj-mail-step b { display: block; color: #111827; margin-bottom: .2rem; }
  .gj-mail-step i { font-style: normal; color: var(--gj-primary); font-weight: 800; margin-right: .3rem; }
  .gj-mail-addr {
    display: inline-block; margin-top: .3rem; padding: .15rem .5rem; border-radius: 8px;
    background: #eef2ff; color: #4338ca; font-family: ui-monospace, monospace; font-size: .85rem;
    word-break: break-all;
  }
  .gj-mail-note { margin-top: .9rem; font-size: .85rem; }

  /* Adhésion sur invitation */
  .st-key-gj_invite {
    margin: 3rem 0 1rem; padding: 1.8rem 1.6rem; border-radius: 22px;
    background: #fff; border: 1px solid #e0e7ff; box-shadow: 0 18px 45px -28px rgba(79, 70, 229, .5);
  }
  .gj-inv-badge {
    display: inline-flex; align-items: center; gap: .4rem; padding: .3rem .75rem; border-radius: 999px;
    background: #eef2ff; color: var(--gj-primary); font-size: .8rem; font-weight: 700;
  }
  .gj-inv-t { font-size: 1.55rem; font-weight: 800; letter-spacing: -.01em; color: #1e1b4b;
    margin: .7rem 0 .4rem; }
  .gj-inv-lead { color: #4b5563; font-size: .95rem; line-height: 1.55; margin: 0 0 1.1rem; }
  .gj-inv-flow { list-style: none; padding: 0; margin: 0; }
  .gj-inv-flow li { display: flex; gap: .75rem; margin-bottom: .8rem; }
  .gj-inv-flow i {
    flex-shrink: 0; width: 1.7rem; height: 1.7rem; border-radius: 50%; display: grid; place-items: center;
    font-style: normal; font-weight: 800; font-size: .82rem; color: #fff;
    background: linear-gradient(135deg, var(--gj-primary), var(--gj-primary-2));
  }
  .gj-inv-flow b { display: block; color: #111827; font-size: .93rem; }
  .gj-inv-flow span { color: #6b7280; font-size: .86rem; }
  .gj-inv-form-t { font-weight: 700; color: #111827; margin-bottom: .2rem; }

  .gj-cta {
    margin: 2rem 0 1rem; padding: 2.4rem 2rem; border-radius: 24px; text-align: center; color: #fff;
    background: linear-gradient(120deg, var(--gj-primary), var(--gj-primary-2));
    box-shadow: 0 24px 50px -24px rgba(79, 70, 229, .8);
  }
  .gj-cta-t { color: #fff; font-size: 1.7rem; font-weight: 800; margin: 0 0 .4rem; }
  .gj-cta p { margin: 0; opacity: .9; }

  @media (max-width: 900px) {
    .gj-features, .gj-stats { grid-template-columns: repeat(2, 1fr); }
    .gj-mail-steps { grid-template-columns: 1fr; }
    .st-key-gj_invite { padding: 1.3rem 1rem; }
    .gj-steps { grid-template-columns: repeat(2, 1fr); row-gap: 1.6rem; }
    .gj-steps::before { display: none; }
    .st-key-gj_hero { padding: 2rem 1.25rem; }
    .gj-preview { transform: none; margin-top: 1rem; }
  }
</style>
"""


def _preview() -> str:
    rows = "".join(
        f'<div class="gj-offer"><div class="gj-score" style="--v:{score};--c:{color}">{score}</div>'
        f'<div><div class="gj-offer-title">{title}</div><div class="gj-offer-meta">{meta}</div>'
        + "".join(f'<span class="gj-tag">{t}</span>' for t in tags)
        + "</div></div>"
        for score, color, title, meta, tags in PREVIEW
    )
    return (
        '<div class="gj-preview"><div class="gj-preview-bar"><i></i><i></i><i></i>'
        "<b>Ton top de la semaine</b></div>"
        f"{rows}"
        f'<div class="gj-float">{icon("thumbs-up", 15)} +10 pts grâce à tes avis</div></div>'
    )


def _mail_block() -> str:
    """Encart expliquant comment transférer ses alertes e-mail APEC vers la boîte de GetIAJob."""
    address = escape(get_settings().imap_user)
    steps = [
        (
            "Crée ton alerte",
            "Sur apec.fr, enregistre une alerte e-mail avec tes critères (poste, lieu, salaire…).",
        ),
        (
            "Transfère-la",
            f'Fais suivre les mails d\'alerte à <span class="gj-mail-addr">{address}</span>, '
            "à la main ou avec une règle de transfert automatique de ta messagerie.",
        ),
        (
            "Retrouve les offres",
            "Elles sont importées, analysées et notées avec les autres lors de tes recherches "
            "(source <b style=\"display:inline\">APEC</b>).",
        ),
    ]
    cards = "".join(
        f'<div class="gj-mail-step"><b><i>{i}</i>{title}</b>{text}</div>'
        for i, (title, text) in enumerate(steps, 1)
    )
    return (
        '<div class="gj-mail"><div class="gj-mail-head">'
        f'<div class="gj-mail-icon">{icon("mail-forward")}</div>'
        '<div><div class="gj-mail-t">Tes alertes e-mail aussi</div>'
        "<p>Tu reçois déjà des offres par mail ? Redirige-les vers GetIAJob pour qu'elles soient "
        "triées avec les autres.</p></div></div>"
        f'<div class="gj-mail-steps">{cards}</div>'
        '<p class="gj-mail-note">Envoie-les <b>depuis l\'adresse de ton compte GetIAJob</b> : c\'est elle '
        "qui permet de te rattacher les offres, visibles par toi seul. Les mails sont supprimés une fois "
        "traités.</p></div>"
    )


def _invite_intro() -> str:
    days = get_settings().invitation_days
    flow = "".join(
        f"<li><i>{i}</i><div><b>{title}</b><span>{text.format(days=days)}</span></div></li>"
        for i, (title, text) in enumerate(INVITE_FLOW, 1)
    )
    return (
        f'<span class="gj-inv-badge">{icon("ticket", 15)} Accès sur invitation</span>'
        '<div class="gj-inv-t">GetIAJob s\'ouvre sur invitation</div>'
        '<p class="gj-inv-lead">Pour garder un service rapide et des modèles d\'IA disponibles pour '
        "chacun, les inscriptions sont validées une par une. Demande ton accès : c'est gratuit et "
        "sans engagement.</p>"
        f'<ol class="gj-inv-flow">{flow}</ol>'
    )


def _invite_block() -> None:
    with st.container(key="gj_invite"):
        left, right = st.columns([1, 1.1], gap="large")
        left.markdown(_invite_intro(), unsafe_allow_html=True)
        with right:
            st.markdown('<div class="gj-inv-form-t">Demander une invitation</div>', unsafe_allow_html=True)
            request_form("home")
            st.caption("Déjà invité(e) ? Clique sur **Se connecter / S'inscrire** en haut à droite.")


def render() -> None:
    user = current_user()
    invite_only = user is None and requests_open()
    st.html(CSS)

    # --- Hero -------------------------------------------------------------------------------- #
    with st.container(key="gj_hero"):
        left, right = st.columns([1.15, 1], gap="large", vertical_alignment="center")
        with left:
            st.markdown(
                # gj-no-sidebar : masque le menu latéral sur l'accueil (cf. layout.CSS)
                '<div class="gj-no-sidebar"><span class="gj-pill"><span class="dot"></span>'
                "IA open source · 100 % locale</span>"
                '<div class="gj-h1">Trouve le job qui te correspond, '
                '<span class="gj-hl">plus vite</span>.</div>'
                '<p class="gj-lead">GetIAJob lit ton CV, parcourt les offres LinkedIn récentes et te '
                "présente les plus pertinentes, notées et argumentées par des agents IA.</p>"
                '<div class="gj-checks">'
                f"<span>{icon('check', 16)} CV analysé en détail</span>"
                f"<span>{icon('check', 16)} Offres fraîches</span>"
                f"<span>{icon('check', 16)} Notes argumentées</span></div></div>",
                unsafe_allow_html=True,
            )
            if user:
                st.write("")
                with st.container(horizontal=True):
                    if st.button("Lancer une recherche", type="primary", icon=":material/rocket_launch:"):
                        go("search")
                    if st.button("Tableau de bord", icon=":material/space_dashboard:"):
                        go("dashboard")
        right.markdown(_preview(), unsafe_allow_html=True)

    # --- Chiffres clés ------------------------------------------------------------------------ #
    st.markdown(
        '<div class="gj-stats">'
        '<div class="gj-stat"><b>3</b><span>agents IA spécialisés</span></div>'
        '<div class="gj-stat"><b>/100</b><span>une note argumentée par offre</span></div>'
        '<div class="gj-stat"><b>24 h</b><span>de fraîcheur possible des annonces</span></div>'
        '<div class="gj-stat"><b>0</b><span>donnée envoyée à une IA tierce</span></div>'
        "</div>",
        unsafe_allow_html=True,
    )

    # --- Fonctionnalités ---------------------------------------------------------------------- #
    cards = "".join(
        f'<div class="gj-feature"><div class="gj-feature-icon">{icon(name)}</div>'
        f'<div class="gj-ft">{title}</div><p>{text}</p></div>'
        for name, title, text in FEATURES
    )
    st.markdown(
        '<div class="gj-section-title">Des agents IA qui travaillent pour toi</div>'
        '<div class="gj-section-sub">Du CV au classement final, chaque étape est automatisée.</div>'
        f'<div class="gj-features">{cards}</div>',
        unsafe_allow_html=True,
    )

    # --- Étapes ------------------------------------------------------------------------------- #
    steps = "".join(
        f'<div class="gj-step"><div class="gj-step-num">{i}</div>'
        f'<div class="gj-st">{title}</div><p>{text}</p></div>'
        for i, (title, text) in enumerate([INVITE_STEP, *STEPS[1:]] if invite_only else STEPS, 1)
    )
    st.markdown(
        '<div class="gj-section-title">Comment ça marche</div>'
        '<div class="gj-section-sub">Quatre étapes, quelques minutes.</div>'
        f'<div class="gj-steps">{steps}</div>',
        unsafe_allow_html=True,
    )

    # --- Alertes e-mail à transférer --------------------------------------------------------- #
    if mailbox_configured():
        st.markdown(_mail_block(), unsafe_allow_html=True)

    # --- Confidentialité ---------------------------------------------------------------------- #
    st.markdown(
        f'<div class="gj-privacy"><div class="gj-privacy-icon">{icon("shield-check", 36)}</div>'
        "<div><b>Tes données restent chez toi</b><p>Les modèles tournent localement via Ollama : "
        "ton CV n'est envoyé à aucun service d'IA tiers, et tout est stocké dans ta propre base."
        "</p></div></div>",
        unsafe_allow_html=True,
    )

    # --- Appel à l'action --------------------------------------------------------------------- #
    if user:
        st.markdown(
            '<div class="gj-cta"><div class="gj-cta-t">Prêt pour ta prochaine recherche ?</div>'
            "<p>Tes avis sur les offres précédentes seront pris en compte.</p></div>",
            unsafe_allow_html=True,
        )
    elif invite_only:
        _invite_block()
    else:
        st.markdown(
            '<div class="gj-cta"><div class="gj-cta-t">Prêt à trouver ton prochain job ?</div>'
            "<p>Clique sur <b>Se connecter / S'inscrire</b> en haut à droite pour commencer.</p></div>",
            unsafe_allow_html=True,
        )
