"""Section « CV » de Mon profil : import, analyse en tâche de fond et consultation.

L'analyse tourne dans un thread du serveur (BackgroundRunner) : changer de page, d'onglet ou
de fenêtre ne l'interrompt pas. L'interface se contente d'interroger son statut.
"""

from __future__ import annotations

from html import escape

import streamlit as st

from getiajob.agents.cv_analyzer import STEPS, SUPPORTED_EXTENSIONS, save_cv
from getiajob.cv_pdf import file_name as cv_pdf_name
from getiajob.cv_pdf import render_cv_pdf
from getiajob.cv_text import detected_sections
from getiajob.llm import uses_ollama_llm
from getiajob.schemas import CVProfile, Experience
from getiajob.services import estimate
from getiajob.services.cvs import (
    CVSummary,
    analysis_progress,
    delete_cv,
    get_cv_file,
    get_cv_text,
    list_cvs,
)
from getiajob.services.users import SessionUser
from getiajob.web.state import get_runner, is_read_only
from getiajob.web.views import cv_edit
from getiajob.web.views.admin_format import budget_status, estimate_label, estimate_note
from getiajob.web.views.cv_edit import SENIORITY
from getiajob.web.views.results import icon

UPLOADER_KEY = "cv_uploader_n"


def _delete(user_id: int, cv_id: int) -> None:
    """Callback du bouton « Confirmer » : supprime le CV et referme le menu de confirmation."""
    delete_cv(user_id, cv_id)
    st.session_state[f"del-menu-{cv_id}"] = False


def _failed_sections(cv_id: int, p: CVProfile) -> None:
    """Sections que le LLM n'a pas pu extraire, tant qu'elles n'ont pas été complétées à la main."""
    still_empty = {
        "identity": not (p.full_name or p.headline),
        "experiences": not p.experiences,
        "education": not p.education,
        "projects": not p.projects,
        "skills": not p.skills,
        "synthesis": not p.target_job_titles,
    }
    missing = [STEPS[k][0] for k in p.failed_sections if k in STEPS and still_empty.get(k, False)]
    if not missing:
        return
    with st.container(horizontal=True, vertical_alignment="center"):
        st.warning(
            f"L'IA n'a pas pu extraire : **{', '.join(missing)}**. Relance l'analyse ou complète la "
            "section à la main.",
            icon=":material/warning:",
        )
        # Profil corrigé à la main : la ré-analyse passe par le bouton dédié (avec confirmation)
        if not p.edited_at and st.button("Ré-analyser", key=f"failed-re-{cv_id}", icon=":material/refresh:"):
            _analyze(cv_id)


def _analyze(cv_id: int, force: bool = True) -> None:
    """Met l'analyse en file d'attente (thread serveur) puis rafraîchit la page."""
    if get_runner().submit_cv_analysis(cv_id, force=force):
        st.toast("Analyse lancée en arrière-plan", icon=":material/hourglass_top:")
    else:
        st.toast("Une analyse de ce CV est déjà en cours", icon=":material/info:")
    st.rerun()


def _duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if seconds < 60:
        return "moins d'1 min"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"


STEP_ICONS = {
    "done": ":material/check_circle:",
    "failed": ":material/error:",
    "running": ":material/progress_activity:",
    "pending": ":material/radio_button_unchecked:",
}


@st.fragment(run_every=3)
def _analysis_progress(cv_id: int) -> None:
    """Rafraîchi toutes les 3 s tant que l'analyse tourne (étapes, temps écoulé / restant)."""
    prog = analysis_progress(cv_id)
    if prog.status not in ("pending", "running"):
        st.rerun()
        return
    with st.container(border=True):
        if prog.status == "pending":
            st.markdown(":material/hourglass_top: **Analyse en file d'attente…**")
        else:
            current = " · ".join(prog.running) or "préparation"
            st.progress(
                prog.done / prog.total,
                text=f"Analyse du CV par l'IA — étape {min(prog.done + 1, prog.total)}/{prog.total} : "
                f"{current}",
            )
            times = []
            if prog.elapsed_s is not None:
                times.append(f":material/schedule: Écoulé : **{_duration(prog.elapsed_s)}**")
            if prog.remaining_s is None:
                times.append("Temps restant estimé après une première analyse complète")
            elif prog.remaining_s > 0:
                times.append(f":material/timer: Reste environ **{_duration(prog.remaining_s)}**")
            else:
                times.append(":material/timer: Plus long que d'habitude, bientôt terminé…")
            st.markdown(" · ".join(times))
            st.caption(" ".join(f"{STEP_ICONS[state]} {label}" for _, label, state in prog.steps))
        st.caption("Tu peux changer de page, d'onglet ou de fenêtre : l'analyse continue sur le serveur.")


# --------------------------------------------------------------------------- #
# Présentation du profil extrait (HTML maison : les SVG passent par st.markdown)
# --------------------------------------------------------------------------- #

CV_ICONS = {
    "graduation": '<path d="M21.42 10.922a1 1 0 0 0-.019-1.838L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 '
    '0 0 1.832l8.57 3.908a2 2 0 0 0 1.66 0z"/><path d="M22 10v6"/><path d="M6 12.5V16a6 3 0 0 0 12 0v-3.5"/>',
    "award": '<path d="m15.477 12.89 1.515 8.526a.5.5 0 0 1-.81.47l-3.58-2.687a1 1 0 0 0-1.197 0l-3.586 '
    '2.686a.5.5 0 0 1-.81-.469l1.514-8.526"/><circle cx="12" cy="8" r="6"/>',
    "rocket": '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 '
    '0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 '
    '11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 '
    '15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
    "external": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 '
    '1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "mail": '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 '
    '0L2 7"/>',
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 '
    "19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 "
    "2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 "
    '16.92z"/>',
    "linkedin": '<path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"/>'
    '<rect width="4" height="12" x="2" y="9"/><circle cx="4" cy="4" r="2"/>',
    "github": '<path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 '
    "0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 "
    '3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4"/><path d="M9 18c-4.51 2-5-2-7-2"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/>'
    '<path d="M2 12h20"/>',
    "languages": '<path d="m5 8 6 6"/><path d="m4 14 6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/>'
    '<path d="m22 22-5-10-5 10"/><path d="M14 18h6"/>',
    "heart": '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-'
    '4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
    "code": '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
    "smile": '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" x2="9.01" '
    'y1="9" y2="9"/><line x1="15" x2="15.01" y1="9" y2="9"/>',
    "ban": '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
    "briefcase": '<path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/><rect width="20" height="14" x="2" '
    'y="6" rx="2"/>',
    "sparkles": '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 '
    "9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 "
    "1.581a.5.5 0 0 1 0 "
    '.964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/>',
    "target": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/>'
    '<circle cx="12" cy="12" r="2"/>',
}


def ico(name: str, size: int = 15) -> str:
    if name not in CV_ICONS:
        return icon(name, size)
    return (
        f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="currentColor" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f"{CV_ICONS[name]}</svg>"
    )


CV_CSS = """
<style>
  /* Carte d'identité du profil */
  .gj-pf { display: flex; gap: 1.1rem; align-items: flex-start; padding: 1.2rem 1.3rem; border-radius: 18px;
    margin: .4rem 0 1rem; border: 1px solid var(--gj-line);
    background: linear-gradient(135deg, rgba(99, 102, 241, .08), rgba(168, 85, 247, .06)); }
  .gj-pf-avatar { flex-shrink: 0; width: 3.6rem; height: 3.6rem; border-radius: 50%; display: grid;
    place-items: center; color: #fff; font-weight: 800; font-size: 1.25rem; letter-spacing: .02em;
    background: linear-gradient(135deg, var(--gj-primary), var(--gj-primary-2));
    box-shadow: 0 10px 24px -10px rgba(79, 70, 229, .8); }
  .gj-pf-main { min-width: 0; flex: 1; }
  .gj-pf-name { font-size: 1.3rem; font-weight: 800; color: var(--gj-head); line-height: 1.25; }
  .gj-pf-head { color: var(--gj-primary); font-weight: 600; margin-top: .1rem; }
  .gj-pf-facts { display: flex; flex-wrap: wrap; gap: .35rem .9rem; margin-top: .55rem; font-size: .85rem;
    color: var(--gj-muted); }
  .gj-pf-facts span, .gj-pf-facts a { display: inline-flex; align-items: center; gap: .3rem; }
  .gj-pf-facts a { color: var(--gj-muted) !important; text-decoration: none; }
  .gj-pf-facts a:hover { color: var(--gj-primary) !important; }
  .gj-pf-level { padding: .1rem .55rem; border-radius: 999px; font-weight: 700; font-size: .75rem;
    background: rgba(99, 102, 241, .14); color: var(--gj-primary); }
  .gj-pf-sum { margin: .8rem 0 0 !important; color: var(--gj-text); font-size: .92rem !important;
    line-height: 1.55; }
  .gj-pf-targets { display: flex; flex-wrap: wrap; align-items: center; gap: .35rem; margin-top: .8rem;
    font-size: .8rem; color: var(--gj-muted); }
  .gj-pf-targets b { display: inline-flex; align-items: center; gap: .3rem; color: var(--gj-text);
    margin-right: .2rem; }
  .gj-pf-targets span { padding: .15rem .6rem; border-radius: 999px; font-weight: 600;
    background: rgba(168, 85, 247, .12); color: #7e22ce; }

  /* Chiffres clés */
  .gj-xp-stats { display: flex; flex-wrap: wrap; gap: .5rem; margin: .2rem 0 1.2rem; }
  .gj-xp-stat { padding: .35rem .8rem; border-radius: 999px; font-size: .82rem; color: var(--gj-muted);
    background: var(--gj-surface-2); border: 1px solid var(--gj-line); }
  .gj-xp-stat b { color: var(--gj-head); font-weight: 800; margin-right: .2rem; }

  /* Frise (expériences, formations) */
  .gj-tl { position: relative; padding-left: 1.9rem; }
  .gj-tl::before { content: ""; position: absolute; left: .55rem; top: .6rem; bottom: .6rem; width: 2px;
    background: linear-gradient(180deg, var(--gj-primary), var(--gj-primary-2) 70%, transparent);
    opacity: .35; }
  .gj-xp { position: relative; margin-bottom: 1rem; }
  .gj-xp-dot { position: absolute; left: -1.82rem; top: 1.15rem; width: 1rem; height: 1rem;
    border-radius: 50%; background: var(--gj-surface); border: 3px solid var(--gj-primary); }
  .gj-xp.current .gj-xp-dot { background: var(--gj-primary); box-shadow: 0 0 0 4px rgba(99, 102, 241, .25); }
  .gj-xp-card, .gj-card2 { background: var(--gj-surface); border: 1px solid var(--gj-line);
    border-radius: 16px; padding: 1rem 1.2rem; transition: border-color .2s ease, box-shadow .2s ease; }
  .gj-xp-card:hover, .gj-card2:hover { border-color: rgba(99, 102, 241, .45);
    box-shadow: 0 14px 30px -22px rgba(49, 46, 129, .6); }
  .gj-xp-top { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
  .gj-xp-title { font-size: 1.08rem; font-weight: 700; color: var(--gj-head); line-height: 1.3; }
  .gj-xp-sub { color: var(--gj-primary); font-weight: 600; font-size: .9rem; margin-top: .15rem; }
  .gj-xp-co { display: flex; flex-wrap: wrap; gap: .2rem .9rem; margin-top: .25rem; font-size: .88rem;
    color: var(--gj-muted); }
  .gj-xp-co span, .gj-xp-when span { display: inline-flex; align-items: center; gap: .3rem; }
  .gj-xp-co .co { color: var(--gj-primary); font-weight: 600; }
  .gj-xp-when { display: flex; flex-direction: column; align-items: flex-end; gap: .25rem; font-size: .82rem;
    color: var(--gj-muted); white-space: nowrap; }
  .gj-xp-when .period { font-weight: 600; color: var(--gj-text); }
  .gj-xp-head { display: flex; flex-wrap: wrap; align-items: center; gap: .3rem .6rem; }
  .gj-xp-tags { display: flex; flex-wrap: wrap; gap: .35rem; }
  .gj-xp-tag { font-size: .72rem; font-weight: 700; padding: .12rem .55rem; border-radius: 999px;
    background: rgba(99, 102, 241, .12); color: var(--gj-primary); }
  .gj-xp-tag.now { background: rgba(34, 197, 94, .14); color: #16a34a; }
  .gj-xp-desc { margin: .7rem 0 0 !important; color: var(--gj-text); font-size: .93rem !important;
    line-height: 1.55; }
  .gj-xp-ach { list-style: none; margin: .6rem 0 0; padding: 0; }
  .gj-xp-ach li { display: flex; gap: .5rem; align-items: flex-start; font-size: .9rem; line-height: 1.5;
    color: var(--gj-text); margin: .25rem 0; }
  .gj-xp-ach svg { flex-shrink: 0; margin-top: .2rem; color: #16a34a; }
  .gj-xp-tech { display: flex; flex-wrap: wrap; gap: .3rem; margin-top: .75rem; padding-top: .7rem;
    border-top: 1px dashed var(--gj-line); }
  .gj-xp-tech span { font-size: .75rem; padding: .14rem .55rem; border-radius: 8px; color: var(--gj-text);
    background: var(--gj-surface-2); border: 1px solid var(--gj-line); }

  /* Grilles de cartes (projets, compétences, langues, certifications) */
  .gj-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: .9rem; }
  .gj-grid { margin-bottom: 1rem; }
  /* Colonnes « masonry » : chaque carte garde sa hauteur (compétences de tailles très variables) */
  .gj-grid.masonry { display: block; columns: 290px; column-gap: .9rem; }
  .gj-grid.masonry > .gj-card2 { break-inside: avoid; margin-bottom: .9rem; }
  .gj-grid.sm { grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: .7rem; }
  .gj-card2 { display: flex; flex-direction: column; }
  .gj-c-head { display: flex; align-items: center; gap: .7rem; }
  .gj-c-icon { flex-shrink: 0; width: 2.3rem; height: 2.3rem; border-radius: 11px; display: grid;
    place-items: center; color: #fff;
    background: linear-gradient(135deg, var(--gj-primary), var(--gj-primary-2)); }
  .gj-c-icon.orange { background: linear-gradient(135deg, #f97316, #ec4899); }
  .gj-c-icon.green { background: linear-gradient(135deg, #16a34a, #0d9488); }
  .gj-c-title { font-weight: 700; color: var(--gj-head); line-height: 1.3; flex: 1; min-width: 0; }
  .gj-c-count { font-size: .75rem; font-weight: 700; color: var(--gj-muted); padding: .1rem .5rem;
    border-radius: 999px; background: var(--gj-surface-2); border: 1px solid var(--gj-line); }
  .gj-c-link { display: inline-flex; align-items: center; gap: .3rem; font-size: .8rem; font-weight: 600;
    color: var(--gj-primary) !important; text-decoration: none; padding: .2rem .6rem; border-radius: 8px;
    border: 1px solid rgba(99, 102, 241, .35); }
  .gj-c-link:hover { background: rgba(99, 102, 241, .1); }
  .gj-c-text { margin: .7rem 0 0 !important; color: var(--gj-text); font-size: .9rem !important;
    line-height: 1.5; flex: 1; }
  .gj-chips2 { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .75rem; }
  .gj-chips2 span { font-size: .78rem; padding: .18rem .6rem; border-radius: 999px; font-weight: 500;
    background: rgba(99, 102, 241, .1); color: #4338ca; }
  .gj-chips2.orange span { background: rgba(249, 115, 22, .12); color: #c2410c; }
  .gj-chips2.pink span { display: inline-flex; align-items: center; gap: .3rem;
    background: rgba(236, 72, 153, .1); color: #be185d; }
  .gj-chips2.pink, .gj-raw .gj-chips2 { margin-bottom: 1rem; }
  .gj-chips2.plain span { border-radius: 8px; color: var(--gj-text); background: var(--gj-surface-2);
    border: 1px solid var(--gj-line); }
  .gj-sec-t { display: flex; align-items: center; gap: .45rem; font-weight: 800; color: var(--gj-head);
    font-size: 1.02rem; margin: 1.4rem 0 .7rem; }
  .gj-sec-t:first-child { margin-top: .2rem; }

  /* Langues */
  .gj-lang-level { font-size: .82rem; color: var(--gj-muted); margin-top: .1rem; }
  .gj-lang-bar { height: .4rem; border-radius: 999px; background: var(--gj-surface-2); margin-top: .8rem;
    overflow: hidden; border: 1px solid var(--gj-line); }
  .gj-lang-bar i { display: block; height: 100%; border-radius: 999px;
    background: linear-gradient(90deg, #16a34a, #0d9488); }

  @media (max-width: 640px) {
    .gj-xp-when { align-items: flex-start; }
    .gj-pf { flex-direction: column; }
  }
</style>
"""

ONGOING = ("présent", "present", "aujourd", "actuel", "en cours", "now", "current")


def _html(content: str) -> None:
    st.markdown(content, unsafe_allow_html=True)


def _stats(items: list[tuple[object, str]]) -> str:
    chips = "".join(f'<span class="gj-xp-stat"><b>{n}</b>{escape(label)}</span>' for n, label in items)
    return f'<div class="gj-xp-stats">{chips}</div>'


def _chips(items: list[str], kind: str = "", icon_name: str | None = None) -> str:
    prefix = ico(icon_name, 12) if icon_name else ""
    spans = "".join(f"<span>{prefix}{escape(i)}</span>" for i in items if i and i.strip())
    return f'<div class="gj-chips2 {kind}">{spans}</div>' if spans else ""


def _safe_url(value: str | None) -> str | None:
    """Lien cliquable uniquement pour une URL web (le reste est affiché en texte)."""
    if not value:
        return None
    v = value.strip()
    if v.lower().startswith(("http://", "https://")):
        return v
    if v.lower().startswith(("www.", "linkedin.com", "github.com", "in/")):
        return "https://" + ("linkedin.com/" + v if v.lower().startswith("in/") else v)
    return None


def _initials(name: str | None) -> str:
    parts = [w for w in (name or "").replace("-", " ").split() if w[:1].isalpha()]
    return ("".join(w[0] for w in parts[:2]) or "CV").upper()


def _header(p: CVProfile) -> None:
    """Carte d'identité : initiales, nom, titre, niveau, coordonnées, résumé, postes ciblés."""
    facts = []
    if p.seniority:
        facts.append(f'<span class="gj-pf-level">{escape(SENIORITY.get(p.seniority, p.seniority))}</span>')
    if p.years_of_experience:
        facts.append(f"<span>{ico('clock', 14)}{p.years_of_experience:g} an(s) d'expérience</span>")
    c = p.contact
    for kind, value in (
        ("map-pin", c.location),
        ("mail", c.email),
        ("phone", c.phone),
        ("linkedin", c.linkedin),
        ("github", c.github),
        ("globe", c.website),
    ):
        if not value:
            continue
        url = f"mailto:{value}" if kind == "mail" else _safe_url(value) if kind != "phone" else None
        inner = f"{ico(kind, 14)}{escape(value)}"
        if url:
            facts.append(f'<a href="{escape(url)}" target="_blank" rel="noopener">{inner}</a>')
        else:
            facts.append(f"<span>{inner}</span>")
    name = escape(p.full_name or "Mon CV")
    head = f'<div class="gj-pf-head">{escape(p.headline)}</div>' if p.headline else ""
    summary = f'<p class="gj-pf-sum">{escape(p.summary)}</p>' if p.summary else ""
    targets = ""
    if p.target_job_titles:
        spans = "".join(f"<span>{escape(t)}</span>" for t in p.target_job_titles)
        targets = f'<div class="gj-pf-targets"><b>{ico("target", 14)}Postes ciblés</b>{spans}</div>'
    _html(
        f'<div class="gj-pf"><div class="gj-pf-avatar">{escape(_initials(p.full_name))}</div>'
        f'<div class="gj-pf-main"><div class="gj-pf-name">{name}</div>{head}'
        f'<div class="gj-pf-facts">{"".join(facts)}</div>{summary}{targets}</div></div>'
    )


def _is_current(e: Experience) -> bool:
    return bool(e.end) and any(w in e.end.lower() for w in ONGOING)


def _when(start: str | None, end: str | None, duration: str | None = None) -> str:
    dates = " – ".join(escape(d) for d in (start, end) if d)
    parts = []
    if dates:
        parts.append(f'<span class="period">{ico("calendar", 14)}{dates}</span>')
    if duration:
        parts.append(f"<span>{ico('clock', 14)}{escape(duration)}</span>")
    return f'<div class="gj-xp-when">{"".join(parts)}</div>'


def _place(org: str | None, location: str | None) -> str:
    co = []
    if org:
        co.append(f'<span class="co">{ico("building", 14)}{escape(org)}</span>')
    if location:
        co.append(f"<span>{ico('map-pin', 14)}{escape(location)}</span>")
    return f'<div class="gj-xp-co">{"".join(co)}</div>' if co else ""


def _timeline_item(top: str, body: str, current: bool = False) -> str:
    return (
        f'<div class="gj-xp{" current" if current else ""}"><div class="gj-xp-dot"></div>'
        f'<div class="gj-xp-card"><div class="gj-xp-top">{top}</div>{body}</div></div>'
    )


def _experience_card(e: Experience) -> str:
    tags = []
    if _is_current(e):
        tags.append('<span class="gj-xp-tag now">En poste</span>')
    if e.contract_type:
        tags.append(f'<span class="gj-xp-tag">{escape(e.contract_type)}</span>')
    # Nature du poste et « En poste » à la hauteur de l'intitulé
    head = f'<div class="gj-xp-title">{escape(e.title)}</div>'
    if tags:
        head += f'<div class="gj-xp-tags">{"".join(tags)}</div>'
    body = ""
    if e.description:
        body += f'<p class="gj-xp-desc">{escape(e.description)}</p>'
    if e.achievements:
        items = "".join(f"<li>{ico('check', 15)}<span>{escape(a)}</span></li>" for a in e.achievements)
        body += f'<ul class="gj-xp-ach">{items}</ul>'
    if e.technologies:
        chips = "".join(f"<span>{escape(t)}</span>" for t in e.technologies)
        body += f'<div class="gj-xp-tech">{chips}</div>'
    top = f'<div><div class="gj-xp-head">{head}</div>{_place(e.company, e.location)}</div>' + _when(
        e.start, e.end, e.duration
    )
    return _timeline_item(top, body, _is_current(e))


def _experiences(p: CVProfile) -> None:
    """Frise chronologique : une carte par poste, dans l'ordre du CV."""
    if not p.experiences:
        st.caption("Aucune expérience détectée.")
        return
    companies = {e.company.strip().lower() for e in p.experiences if e.company}
    techs = {t.strip().lower() for e in p.experiences for t in e.technologies}
    stats: list[tuple[object, str]] = [(len(p.experiences), "poste(s)"), (len(companies), "entreprise(s)")]
    if p.years_of_experience:
        stats.append((f"{p.years_of_experience:g}", "an(s) d'expérience"))
    if techs:
        stats.append((len(techs), "technologie(s) citée(s)"))
    cards = "".join(_experience_card(e) for e in p.experiences)
    _html(f'{_stats(stats)}<div class="gj-tl">{cards}</div>')


def _education(p: CVProfile) -> None:
    """Frise des diplômes, puis certifications en cartes."""
    if not (p.education or p.certifications):
        st.caption("Aucune formation détectée.")
        return
    html = _stats(
        [(len(p.education), "formation(s)")]
        + ([(len(p.certifications), "certification(s)")] if p.certifications else [])
    )
    if p.education:
        items = []
        for ed in p.education:
            sub = f'<div class="gj-xp-sub">{escape(ed.field)}</div>' if ed.field else ""
            top = (
                f'<div><div class="gj-xp-title">{escape(ed.degree)}</div>{sub}'
                f"{_place(ed.school, ed.location)}</div>{_when(ed.start, ed.end)}"
            )
            body = _chips(ed.details, "plain")
            items.append(
                _timeline_item(top, body, bool(ed.end) and any(w in ed.end.lower() for w in ONGOING))
            )
        html += f'<div class="gj-tl">{"".join(items)}</div>'
    if p.certifications:
        cards = "".join(
            f'<div class="gj-card2"><div class="gj-c-head"><div class="gj-c-icon green">{ico("award", 18)}'
            f'</div><div class="gj-c-title">{escape(c)}</div></div></div>'
            for c in p.certifications
        )
        html += f'<div class="gj-sec-t">{ico("award", 18)}Certifications</div>'
        html += f'<div class="gj-grid sm">{cards}</div>'
    _html(html)


def _projects(p: CVProfile) -> None:
    """Grille de cartes : nom, lien éventuel, description, technologies."""
    if not p.projects:
        st.caption("Aucun projet détecté.")
        return
    cards = []
    for pr in p.projects:
        url = _safe_url(pr.url)
        link = (
            f'<a class="gj-c-link" href="{escape(url)}" target="_blank" rel="noopener">'
            f"{ico('external', 13)}Voir</a>"
            if url
            else ""
        )
        desc = f'<p class="gj-c-text">{escape(pr.description)}</p>' if pr.description else ""
        cards.append(
            f'<div class="gj-card2"><div class="gj-c-head"><div class="gj-c-icon">{ico("rocket", 18)}</div>'
            f'<div class="gj-c-title">{escape(pr.name)}</div>{link}</div>{desc}'
            f"{_chips(pr.technologies, 'plain')}</div>"
        )
    techs = {t.strip().lower() for pr in p.projects for t in pr.technologies}
    stats = [(len(p.projects), "projet(s)")] + ([(len(techs), "technologie(s)")] if techs else [])
    _html(f'{_stats(stats)}<div class="gj-grid">{"".join(cards)}</div>')


def _merged_skills(p: CVProfile) -> list[tuple[str, list[str]]]:
    """L'extraction section par section peut produire deux fois la même catégorie : on les fusionne."""
    return p.merged_skills()


def _skills(p: CVProfile) -> None:
    """Une carte par catégorie de compétences techniques, puis le savoir-être."""
    if not (p.skills or p.soft_skills):
        st.caption("Aucune compétence détectée.")
        return
    merged = _merged_skills(p)
    n_hard = sum(len(items) for _, items in merged)
    stats: list[tuple[object, str]] = [
        (n_hard, "compétence(s) technique(s)"),
        (len(merged), "catégorie(s)"),
    ]
    if p.soft_skills:
        stats.append((len(p.soft_skills), "savoir-être"))
    cards = [
        f'<div class="gj-card2"><div class="gj-c-head"><div class="gj-c-icon">{ico("code", 18)}</div>'
        f'<div class="gj-c-title">{escape(name)}</div><span class="gj-c-count">{len(items)}</span>'
        f"</div>{_chips(items)}</div>"
        for name, items in merged
    ]
    if p.soft_skills:
        cards.append(
            f'<div class="gj-card2"><div class="gj-c-head"><div class="gj-c-icon orange">{ico("smile", 18)}'
            f'</div><div class="gj-c-title">Savoir-être</div><span class="gj-c-count">{len(p.soft_skills)}'
            f"</span></div>{_chips(p.soft_skills, 'orange')}</div>"
        )
    _html(f'{_stats(stats)}<div class="gj-grid masonry">{"".join(cards)}</div>')


# Niveau de langue → remplissage de la jauge (premier motif trouvé)
LEVELS = (
    (("natif", "native", "maternelle", "bilingue", "c2"), 100),
    (("c1", "courant", "fluent", "avancé", "advanced"), 85),
    (("b2", "professionnel"), 70),
    (("b1", "intermédiaire", "intermediate"), 55),
    (("a2", "scolaire"), 35),
    (("a1", "notions", "débutant", "beginner", "élémentaire"), 20),
)


def _level_pct(level: str | None) -> int | None:
    low = (level or "").lower()
    return next((pct for words, pct in LEVELS if any(w in low for w in words)), None)


def _languages_interests(p: CVProfile) -> None:
    html = f'<div class="gj-sec-t">{ico("languages", 18)}Langues</div>'
    if p.languages:
        cards = []
        for lang in p.languages:
            pct = _level_pct(lang.level)
            bar = f'<div class="gj-lang-bar"><i style="width:{pct}%"></i></div>' if pct else ""
            level = f'<div class="gj-lang-level">{escape(lang.level)}</div>' if lang.level else ""
            cards.append(
                f'<div class="gj-card2"><div class="gj-c-head"><div class="gj-c-icon green">'
                f'{ico("languages", 18)}</div><div class="gj-c-title">{escape(lang.language)}{level}</div>'
                f"</div>{bar}</div>"
            )
        html += f'<div class="gj-grid sm">{"".join(cards)}</div>'
    else:
        html += '<div class="gj-lang-level">Aucune langue détectée.</div>'
    html += f'<div class="gj-sec-t">{ico("heart", 18)}Centres d\'intérêt</div>'
    html += (
        _chips(p.interests, "pink", "heart")
        or '<div class="gj-lang-level">Aucun centre d\'intérêt détecté.</div>'
    )
    _html(html)


def _raw_text(user_id: int, cv_id: int) -> None:
    text = get_cv_text(user_id, cv_id) or ""
    sections = detected_sections(text)
    _html(
        '<div class="gj-raw"><div class="gj-lang-level">Texte lu dans le fichier, remis dans l\'ordre de '
        "lecture. Sections détectées :</div>"
        + (_chips(sections, "plain") or '<div class="gj-lang-level">aucune</div>')
        + "</div>"
    )
    st.code(text, language=None, wrap_lines=True)


def _edit_toggle(key: str, label: str) -> bool:
    """Interrupteur « Modifier » aligné à droite : bascule un bloc en mode correction."""
    with st.container(horizontal=True, horizontal_alignment="right"):
        return st.toggle(label, key=key)


def _cv_card(user_id: int, cv: CVSummary, is_current: bool) -> None:
    with st.container(border=True):
        c1, c2 = st.columns([5, 2])
        title = f"**{cv.file_name}**" + ("  :blue-badge[CV le plus récent]" if is_current else "")
        if cv.profile is not None and cv.profile.edited_at:
            title += "  :violet-badge[:material/edit: Corrigé manuellement]"
        c1.markdown(f"{title}  \n:gray[Importé le {cv.created_at:%d/%m/%Y à %H:%M}]")
        if not cv.analyzing and (cv_text := get_cv_text(user_id, cv.id)):
            est = estimate.cv_analysis(cv_text)
            msg, level = budget_status(est.total, user_id)
            c1.caption(
                f":material/token: {'Analyse' if cv.profile is None else 'Ré-analyse'} estimée : "
                f"{estimate_label(est)}{estimate_note(est)}"
                + (f" · :{'red' if level == 'block' else 'orange'}[{msg}]" if msg and level != "ok" else ""),
                help="Tokens envoyés calculés d'après le texte du CV ; tokens générés d'après les "
                "dernières analyses faites avec le même modèle (Admin › Supervision › Consommation IA).",
            )

        # Actions groupées à droite, côte à côte (et non réparties sur trois colonnes)
        with c2.container(horizontal=True, horizontal_alignment="right", gap="small"):
            file = get_cv_file(user_id, cv.id)
            if file:
                st.download_button(
                    "",
                    data=file[1],
                    file_name=file[0],
                    icon=":material/download:",
                    key=f"dl-{cv.id}",
                    help="Télécharger",
                )
            if cv.profile is not None and not cv.profile.is_outdated:
                profile = cv.profile
                st.download_button(
                    "",
                    data=lambda: render_cv_pdf(profile),  # généré seulement au clic
                    file_name=cv_pdf_name(profile),
                    mime="application/pdf",
                    icon=":material/picture_as_pdf:",
                    key=f"pdf-{cv.id}",
                    help="Générer un CV au format PDF à partir des informations ci-dessous",
                    on_click="ignore",
                )
            if cv.profile is not None and cv.profile.edited_at:
                # Profil corrigé à la main : la ré-analyse écraserait les corrections → confirmation
                with st.popover("", icon=":material/refresh:", help="Ré-analyser", disabled=cv.analyzing):
                    st.write("Relancer l'analyse remplacera tes corrections manuelles. Continuer ?")
                    if st.button("Ré-analyser quand même", type="primary", key=f"re-{cv.id}"):
                        _analyze(cv.id)
            elif st.button(
                "",
                icon=":material/refresh:",
                key=f"re-{cv.id}",
                help="Ré-analyser",
                disabled=cv.analyzing,
            ):
                _analyze(cv.id)
            # Clé propre au CV : le menu se referme à la suppression et ne se rouvre pas sur le CV suivant
            with st.popover(
                "", icon=":material/delete:", help="Supprimer", key=f"del-menu-{cv.id}", on_change="rerun"
            ):
                st.write("Supprimer ce CV et ses scores ?")
                st.button(
                    "Confirmer",
                    type="primary",
                    key=f"del-{cv.id}",
                    on_click=_delete,
                    args=(user_id, cv.id),
                    disabled=is_read_only(),
                )

        p = cv.profile
        if cv.analyzing:
            _analysis_progress(cv.id)
            if p is None:
                return
        elif cv.analysis_status == "failed":
            if "TokenBudgetExceeded" in (cv.analysis_error or ""):
                hint = "limite de tokens atteinte"
            elif uses_ollama_llm():
                hint = "Ollama démarré ? modèle téléchargé ?"
            else:
                hint = "API du LLM joignable ? clé API valide ? (Admin › Paramètres › Tester)"
            st.error(
                f"L'IA n'a pas pu analyser ce CV ({hint}).  \n`{(cv.analysis_error or '')[:300]}`",
                icon=":material/error:",
            )
            if st.button("Réessayer", key=f"retry-{cv.id}", icon=":material/refresh:"):
                _analyze(cv.id)
            if p is None:
                return
        elif p is None or p.is_outdated:
            if p is None:
                st.warning("Ce CV n'a pas encore été analysé.")
            else:
                st.warning(
                    "Ce CV a été analysé avec une ancienne version de l'extraction : relance l'analyse "
                    "pour obtenir le découpage détaillé (expériences, formation, compétences, langues…)."
                )
            if st.button("Analyser maintenant", key=f"an-{cv.id}", type="primary"):
                _analyze(cv.id)
            if p is None:
                return

        st.html(CV_CSS)  # styles seuls : n'occupe aucune place
        _failed_sections(cv.id, p)
        n_skills = sum(len(items) for _, items in _merged_skills(p)) + len(p.soft_skills)
        if _edit_toggle(f"edit-id-{cv.id}", "Modifier l'identité"):
            cv_edit.edit_identity(user_id, cv.id, p)
        else:
            _header(p)
        tabs = st.tabs(
            [
                f":material/work: Expériences ({len(p.experiences)})",
                f":material/school: Formation ({len(p.education)})",
                f":material/rocket_launch: Projets ({len(p.projects)})",
                f":material/psychology: Compétences ({n_skills})",
                f":material/translate: Langues & intérêts ({len(p.languages)})",
                ":material/article: Texte extrait",
            ]
        )
        uid, cid = user_id, cv.id
        blocks = [
            (lambda: _experiences(p), lambda: cv_edit.edit_experiences(uid, cid, p)),
            (lambda: _education(p), lambda: cv_edit.edit_education(uid, cid, p)),
            (lambda: _projects(p), lambda: cv_edit.edit_projects(uid, cid, p)),
            (lambda: _skills(p), lambda: cv_edit.edit_skills(uid, cid, p, _merged_skills(p))),
            (lambda: _languages_interests(p), lambda: cv_edit.edit_languages_interests(uid, cid, p)),
        ]
        for i, (show, edit) in enumerate(blocks):
            with tabs[i]:
                if _edit_toggle(f"edit-{i}-{cv.id}", "Modifier ce bloc"):
                    edit()
                else:
                    show()
        with tabs[5]:
            _raw_text(user_id, cv.id)


def render_section(user: SessionUser) -> None:
    st.caption("Ton CV est stocké dans ta base de données et analysé localement par l'IA.")

    with st.container(border=True):
        n = st.session_state.setdefault(UPLOADER_KEY, 0)
        uploaded = st.file_uploader(
            "Importer un CV",
            type=list(SUPPORTED_EXTENSIONS),
            help="PDF texte, Word, Markdown ou texte.",
            key=f"cv-upload-{n}",
        )
        if uploaded and st.button("Importer et analyser", type="primary", icon=":material/upload:"):
            try:
                cv_id = save_cv(uploaded.getvalue(), uploaded.name, user_id=user.id)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state[UPLOADER_KEY] = n + 1  # vide le champ d'import
                _analyze(cv_id, force=False)

    cvs = list_cvs(user.id)
    if not cvs:
        st.info("Aucun CV pour l'instant. Importe ton CV pour commencer.", icon=":material/upload_file:")
        return
    for i, cv in enumerate(cvs):
        _cv_card(user.id, cv, is_current=i == 0)
