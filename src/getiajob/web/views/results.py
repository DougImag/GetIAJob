"""Historique des recherches, suivi en direct et résultats détaillés."""

from __future__ import annotations

from datetime import UTC, date, datetime
from html import escape

import streamlit as st

from getiajob.db import SearchSession
from getiajob.report import render_markdown
from getiajob.scraping.base import SOURCE_LABELS
from getiajob.services.applications import ApplicationError, AppliedMark, applied_map, mark_applied
from getiajob.services.cvs import list_cvs
from getiajob.services.feedback import (
    DISLIKE,
    LIKE,
    REASONS,
    Feedback,
    delete_feedback,
    exclude_company,
    feedback_map,
    set_feedback,
)
from getiajob.services.searches import (
    STATUS_LABELS,
    STEP_LABELS,
    delete_search,
    get_search,
    list_searches,
    queue_position,
    rerank_search,
)
from getiajob.web.layout import EXPERIENCE_LABELS, FRESHNESS_LABELS, REMOTE_LABELS, score_color
from getiajob.web.state import APPLICATION_FOCUS_KEY, get_runner, go, page, require_user

SHORT_SKILL = 32  # au-delà, une « compétence » est une phrase : pas affichée sur la carte
VERDICTS = {"green": "Très pertinent", "orange": "Pertinent", "red": "Peu pertinent"}
COLORS = {"green": "#16a34a", "orange": "#ea580c", "red": "#dc2626"}
STATUS_STYLE = {"done": "ok", "failed": "ko", "running": "run", "pending": "run"}

# Icônes Lucide (https://lucide.dev, licence ISC)
ICONS = {
    "building": '<rect width="16" height="20" x="4" y="2" rx="2"/><path d="M9 22v-4h6v4"/>'
    '<path d="M8 6h.01"/><path d="M16 6h.01"/><path d="M12 6h.01"/><path d="M12 10h.01"/>'
    '<path d="M12 14h.01"/>'
    '<path d="M16 10h.01"/><path d="M16 14h.01"/><path d="M8 10h.01"/><path d="M8 14h.01"/>',
    "map-pin": '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 '
    '10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>',
    "calendar": '<path d="M8 2v4"/><path d="M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/>'
    '<path d="M3 10h18"/>',
    "euro": '<path d="M4 10h12"/><path d="M4 14h9"/><path d="M19 6a7.7 7.7 0 0 0-5.2-2A7.9 7.9 0 0 0 6 12c0 '
    '4.4 3.5 8 7.8 8 2 0 3.8-.8 5.2-2"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16.5 12"/>',
    "house": '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-'
    '5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "list": '<path d="M3 12h.01"/><path d="M3 18h.01"/><path d="M3 6h.01"/><path d="M8 12h13"/>'
    '<path d="M8 18h13"/><path d="M8 6h13"/>',
    "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
    '<path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
    "user": '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
    '<path d="M12 9v4"/><path d="M12 17h.01"/>',
    "sliders": '<line x1="21" x2="14" y1="4" y2="4"/><line x1="10" x2="3" y1="4" y2="4"/><line x1="21" '
    'x2="12" y1="12" y2="12"/><line x1="8" x2="3" y1="12" y2="12"/><line x1="21" x2="16" y1="20" '
    'y2="20"/><line x1="12" x2="3" y1="20" y2="20"/><line x1="14" x2="14" y1="2" y2="6"/><line x1="8" '
    'x2="8" y1="10" y2="14"/><line x1="16" x2="16" y1="18" y2="22"/>',
}


def icon(name: str, size: int = 15) -> str:
    return (
        f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="currentColor" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f"{ICONS[name]}</svg>"
    )


CSS = """
<style>
  /* En-tête de la recherche */
  .gj-sh-top { display: flex; align-items: center; gap: .7rem; flex-wrap: wrap; }
  .gj-sh-title { font-size: 1.55rem; font-weight: 800; color: #1e1b4b; letter-spacing: -.01em; }
  .gj-status { font-size: .78rem; font-weight: 700; padding: .2rem .65rem; border-radius: 999px; }
  .gj-status.ok { background: #dcfce7; color: #166534; }
  .gj-status.ko { background: #fee2e2; color: #991b1b; }
  .gj-status.run { background: #e0e7ff; color: #3730a3; }
  .gj-sh-sub { color: #6b7280; font-size: .88rem; margin: .25rem 0 .8rem; display: flex; gap: 1rem;
    flex-wrap: wrap; }
  .gj-sh-sub span, .gj-chip, .gj-meta span { display: inline-flex; align-items: center; gap: .35rem; }
  .gj-chips { display: flex; flex-wrap: wrap; gap: .45rem; padding-bottom: .35rem; }
  .gj-chip { padding: .3rem .7rem; border-radius: 999px; background: #f5f3ff; border: 1px solid #e4e0fb;
    color: #3730a3; font-size: .82rem; font-weight: 500; }
  .gj-chip svg { opacity: .75; }

  /* Chiffres clés */
  .gj-kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: .8rem; margin: .4rem 0 .2rem; }
  .gj-kpi { background: #fff; border: 1px solid #eceafd; border-radius: 14px; padding: .8rem 1rem; }
  .gj-kpi b { display: block; font-size: 1.55rem; font-weight: 800; color: #1e1b4b; letter-spacing: -.02em; }
  .gj-kpi span { color: #6b7280; font-size: .8rem; }

  /* Carte d'offre */
  [class*="st-key-gj_card_"] {
    border-radius: 16px !important; transition: box-shadow .2s ease, border-color .2s ease;
  }
  [class*="st-key-gj_card_"]:hover { border-color: rgba(79, 70, 229, .35) !important;
    box-shadow: 0 14px 34px -20px rgba(49, 46, 129, .45); }
  .gj-card { display: flex; gap: 1.1rem; align-items: flex-start; }
  .gj-ring {
    width: 4.2rem; height: 4.2rem; border-radius: 50%; flex-shrink: 0; display: grid; place-items: center;
    background: radial-gradient(#fff 60%, transparent 62%),
      conic-gradient(var(--c) calc(var(--v) * 1%), #eef0f4 0);
  }
  .gj-ring b { font-size: 1.25rem; font-weight: 800; color: #111827; line-height: 1; }
  .gj-ring small { display: block; font-size: .62rem; color: #9ca3af; text-align: center; }
  .gj-card-body { flex: 1; min-width: 0; }
  .gj-card-title { display: flex; align-items: baseline; gap: .5rem; flex-wrap: wrap; }
  .gj-rank { color: #9ca3af; font-weight: 700; font-size: .9rem; }
  .gj-card-title a { font-size: 1.12rem; font-weight: 700; color: #1e1b4b; text-decoration: none; }
  .gj-card-title a:hover { color: var(--gj-primary); text-decoration: underline; }
  .gj-meta { display: flex; flex-wrap: wrap; gap: .3rem 1rem; color: #6b7280; font-size: .85rem;
    margin: .3rem 0 .55rem; }
  .gj-meta span.gj-src { font-size: .7rem; font-weight: 700; padding: .08rem .5rem; border-radius: 999px;
    color: var(--sc); background: color-mix(in srgb, var(--sc) 13%, transparent); }
  .gj-verdict { display: inline-block; font-size: .75rem; font-weight: 700; padding: .15rem .55rem;
    border-radius: 999px; margin-right: .45rem; color: var(--c);
    background: color-mix(in srgb, var(--c) 12%, #fff); }
  .gj-summary { color: #374151; font-size: .93rem; line-height: 1.5; }
  .gj-skills { margin-top: .55rem; display: flex; flex-wrap: wrap; gap: .3rem; }
  .gj-skill { font-size: .75rem; padding: .12rem .55rem; border-radius: 999px; }
  .gj-skill.ok { background: #dcfce7; color: #166534; }
  .gj-skill.miss { background: #ffedd5; color: #9a3412; }
  .gj-skill.more { background: #f3f4f6; color: #4b5563; }
  .gj-skill-t { font-weight: 700; font-size: .85rem; color: #374151; margin: .9rem 0 .35rem; }
  .gj-adj { display: inline-flex; align-items: center; gap: .3rem; margin-top: .5rem; font-size: .8rem;
    color: #5b21b6; background: #f3e8ff; padding: .15rem .55rem; border-radius: 8px; }

  /* Analyse détaillée */
  .gj-box { border-radius: 12px; padding: .8rem 1rem; height: 100%; }
  .gj-box.pros { background: #f0fdf4; border: 1px solid #bbf7d0; }
  .gj-box.cons { background: #fff7ed; border: 1px solid #fed7aa; }
  .gj-box-t { font-weight: 700; font-size: .9rem; margin-bottom: .4rem; display: flex; gap: .4rem;
    align-items: center; }
  .gj-box.pros .gj-box-t { color: #166534; } .gj-box.cons .gj-box-t { color: #9a3412; }
  .gj-box ul { margin: 0; padding-left: 1.1rem; } .gj-box li { font-size: .88rem; color: #374151; }
  .gj-bars { display: grid; grid-template-columns: auto 1fr auto; gap: .35rem .8rem; align-items: center;
    margin-top: .9rem; font-size: .82rem; color: #4b5563; }
  .gj-bar { height: .45rem; border-radius: 999px; background: #eef0f4; overflow: hidden; }
  .gj-bar i { display: block; height: 100%; border-radius: 999px;
    background: linear-gradient(90deg, var(--gj-primary), var(--gj-primary-2)); }

  @media (max-width: 900px) { .gj-kpis { grid-template-columns: repeat(2, 1fr); } }
</style>
"""


def _label(s: SearchSession) -> str:
    status = STATUS_LABELS.get(s.status, s.status)
    return f"{s.name} — {s.created_at:%d/%m/%Y %H:%M} · {status}"


def _ago(iso_day: str | None) -> str | None:
    if not iso_day:
        return None
    days = (date.today() - date.fromisoformat(iso_day[:10])).days
    if days <= 0:
        return "aujourd'hui"
    if days == 1:
        return "hier"
    return f"il y a {days} j"


def _duration(s: SearchSession) -> str:
    if not (s.finished_at and s.started_at):
        return "—"
    secs = int((s.finished_at - s.started_at).total_seconds())
    return f"{secs // 60} min {secs % 60:02d}" if secs >= 60 else f"{secs} s"


@st.fragment(run_every=3)
def _live_status(user_id: int, search_id: int) -> None:
    """Rafraîchi toutes les 3 s tant que la recherche tourne, puis recharge la page."""
    s = get_search(user_id, search_id)
    if s is None or s.status not in ("pending", "running"):
        st.rerun()
        return
    if s.status == "pending":
        ahead = queue_position(search_id)
        st.info(f"En file d'attente ({ahead} recherche(s) avant la tienne)…", icon=":material/hourglass_top:")
        return

    skip = s.params.get("skip_scrape")
    steps = [k for k in STEP_LABELS if not (skip and k == "scrape_jobs")]
    idx = steps.index(s.step) if s.step in steps else 0
    elapsed = datetime.now(UTC) - (s.started_at or datetime.now(UTC))
    st.progress(
        (idx + 0.5) / len(steps),
        text=f"Étape {idx + 1}/{len(steps)} : {STEP_LABELS.get(s.step, '…')}",
    )
    st.caption(
        f"Démarrée depuis {int(elapsed.total_seconds() // 60)} min "
        f"{int(elapsed.total_seconds() % 60)} s. Tu peux quitter cette page, la recherche continue."
    )


# --------------------------------------------------------------------------- #
# En-tête de la recherche
# --------------------------------------------------------------------------- #


def _criteria_chips(s: SearchSession) -> str:
    prefs = s.params.get("preferences", {})
    search, ranking = prefs.get("search", {}), prefs.get("ranking", {})
    days = search.get("days")
    chips = [
        ("clock", f"Moins de {FRESHNESS_LABELS.get(days, f'{days} jours')}" if days else "Toutes dates"),
        ("map-pin", ", ".join(search.get("locations", [])) or "Toutes localisations"),
        ("list", f"Top {ranking.get('top_n', '?')}"),
    ]
    if remote := search.get("remote"):
        chips.append(("house", " / ".join(REMOTE_LABELS.get(r, r) for r in remote)))
    if levels := search.get("experience_levels"):
        chips.append(("user", ", ".join(EXPERIENCE_LABELS.get(lv, lv) for lv in levels)))
    if keywords := search.get("keywords"):
        chips.append(("search", ", ".join(keywords)))
    if s.params.get("skip_scrape"):
        chips.append(("sliders", "Tri des offres déjà collectées"))
    if ranking.get("hidden_job_ids") or ranking.get("liked_job_ids"):
        n = len(ranking.get("hidden_job_ids", [])) + len(ranking.get("liked_job_ids", []))
        chips.append(("sliders", f"{n} avis pris en compte"))
    return "".join(f'<span class="gj-chip">{icon(i, 13)}{escape(t)}</span>' for i, t in chips)


def _search_header(user_id: int, s: SearchSession) -> None:
    cv = next((c for c in list_cvs(user_id) if c.id == s.cv_id), None)
    with st.container(border=True):
        head, actions = st.columns([5, 2], vertical_alignment="top")
        sub = [f"<span>{icon('calendar')} {s.created_at:%d/%m/%Y à %H:%M}</span>"]
        if cv:
            sub.append(f"<span>{icon('file')} {escape(cv.file_name)}</span>")
        head.markdown(
            f'<div class="gj-sh-top"><span class="gj-sh-title">{escape(s.name)}</span>'
            f'<span class="gj-status {STATUS_STYLE.get(s.status, "run")}">'
            f"{STATUS_LABELS.get(s.status, s.status)}</span></div>"
            f'<div class="gj-sh-sub">{"".join(sub)}</div>'
            f'<div class="gj-chips">{_criteria_chips(s)}</div>',
            unsafe_allow_html=True,
        )
        with actions.container(horizontal=True, horizontal_alignment="right", gap="small"):
            if s.status == "done" and s.results:
                headline = cv.profile.headline if cv and cv.profile else ""
                st.download_button(
                    "Rapport",
                    render_markdown(s.results, headline, s.created_at.date()),
                    file_name=f"getiajob_recherche_{s.id}.md",
                    icon=":material/download:",
                    help="Télécharger le rapport Markdown",
                )
            if s.status in ("done", "failed"):
                with st.popover("", icon=":material/delete:", help="Supprimer cette recherche"):
                    st.write("Supprimer cette recherche de l'historique ?")
                    if st.button("Confirmer", type="primary", key=f"del-search-{s.id}"):
                        delete_search(user_id, s.id)
                        st.session_state.pop("selected_search", None)
                        st.rerun()


def _kpis(s: SearchSession, results: list[dict]) -> None:
    scores = [r["final_score"] for r in results]
    best = f"{max(scores):.0f}" if scores else "—"
    avg = f"{sum(scores) / len(scores):.0f}" if scores else "—"
    items = [
        (s.n_offers if s.n_offers is not None else "—", "offres collectées"),
        (len(results), "offres classées"),
        (best, "meilleur score"),
        (avg, "score moyen"),
        (_duration(s), "durée"),
    ]
    st.markdown(
        '<div class="gj-kpis">'
        + "".join(f'<div class="gj-kpi"><b>{v}</b><span>{label}</span></div>' for v, label in items)
        + "</div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Carte d'une offre
# --------------------------------------------------------------------------- #

SOURCE_COLORS = {"linkedin": "#0a66c2", "francetravail": "#e2001a", "adzuna": "#16a34a", "apec": "#7c3aed"}


def _card_header(rank: int, r: dict) -> str:
    color = score_color(r["final_score"])
    meta = []
    source = r.get("source") or "linkedin"
    meta.append(
        f'<span class="gj-src" style="--sc:{SOURCE_COLORS.get(source, "#6b7280")}">'
        f"{escape(SOURCE_LABELS.get(source, source))}</span>"
    )
    if r.get("company"):
        meta.append(f"<span>{icon('building')} {escape(r['company'])}</span>")
    if r.get("location"):
        meta.append(f"<span>{icon('map-pin')} {escape(r['location'])}</span>")
    if ago := _ago(r.get("posted_at")):
        meta.append(f"<span>{icon('calendar')} Publiée {ago}</span>")
    if r.get("salary"):
        meta.append(f"<span>{icon('euro')} {escape(r['salary'])}</span>")

    # Sur la carte : compétences courtes uniquement (l'IA renvoie parfois des phrases entières,
    # elles restent consultables dans « Analyse détaillée »).
    matching = r.get("matching_skills") or []
    short = [x for x in matching if len(x) <= SHORT_SKILL][:6]
    skills = "".join(f'<span class="gj-skill ok">{escape(x)}</span>' for x in short)
    if rest := len(matching) - len(short):
        skills += f'<span class="gj-skill more">+{rest} dans l’analyse</span>'
    adj = r.get("feedback_adjustment") or 0
    adj_html = (
        f'<div class="gj-adj">{icon("sliders", 13)} {adj:+.0f} pts selon tes avis '
        f"({'proche d’offres aimées' if adj > 0 else 'proche d’offres écartées'})</div>"
        if adj
        else ""
    )
    summary = (
        f'<div class="gj-summary"><span class="gj-verdict" style="--c:{COLORS[color]}">'
        f"{VERDICTS[color]}</span>{escape(r.get('summary') or '')}</div>"
    )
    return (
        f'<div class="gj-card"><div class="gj-ring" style="--v:{r["final_score"]:.0f};--c:{COLORS[color]}">'
        f"<div><b>{r['final_score']:.0f}</b><small>/ 100</small></div></div>"
        f'<div class="gj-card-body"><div class="gj-card-title"><span class="gj-rank">#{rank}</span>'
        f'<a href="{escape(r["url"], quote=True)}" target="_blank" rel="noopener">'
        f"{escape(r['title'])}</a></div>"
        f'<div class="gj-meta">{"".join(meta)}</div>{summary}'
        f"{f'<div class=gj-skills>{skills}</div>' if skills else ''}{adj_html}</div></div>"
    )


def _analysis(r: dict) -> None:
    c1, c2 = st.columns(2)
    for col, kind, title, ico, items in (
        (c1, "pros", "Points forts", "check", r.get("pros") or []),
        (c2, "cons", "Points de vigilance", "alert", r.get("cons") or []),
    ):
        lis = "".join(f"<li>{escape(x)}</li>" for x in items) or "<li>—</li>"
        col.markdown(
            f'<div class="gj-box {kind}"><div class="gj-box-t">{icon(ico)} {title}</div><ul>{lis}</ul></div>',
            unsafe_allow_html=True,
        )
    for title, kind, items in (
        ("Compétences en adéquation", "ok", r.get("matching_skills") or []),
        ("Compétences à renforcer", "miss", r.get("missing_skills") or []),
    ):
        if items:
            chips = "".join(f'<span class="gj-skill {kind}">{escape(x)}</span>' for x in items)
            st.markdown(
                f'<div class="gj-skill-t">{title}</div><div class="gj-skills">{chips}</div>',
                unsafe_allow_html=True,
            )
    llm = r.get("llm_score")
    sim = round((r.get("similarity") or 0) * 100)
    rows = [("Note de l'IA", llm, f"{llm:.0f} / 100" if llm is not None else "n/a")]
    rows.append(("Proximité CV ↔ offre", sim, f"{sim} / 100"))
    bars = "".join(
        f'<span>{label}</span><div class="gj-bar"><i style="width:{max(0, min(100, v or 0))}%"></i></div>'
        f"<b>{txt}</b>"
        for label, v, txt in rows
    )
    if adj := r.get("feedback_adjustment"):
        bars += f"<span>Ajustement selon tes avis</span><span></span><b>{adj:+.0f} pts</b>"
    st.markdown(f'<div class="gj-bars">{bars}</div>', unsafe_allow_html=True)


def _feedback_controls(user_id: int, search_id: int, r: dict, fb: Feedback | None) -> None:
    """Avis sur l'offre : réutilisé par les prochaines recherches pour affiner le tri."""
    job_id = r.get("job_id")
    if not job_id:
        return
    uid = f"{search_id}-{job_id}"
    if fb is not None:
        if fb.liked:
            text = ":green-badge[:material/thumb_up: Tu as aimé cette offre]"
        else:
            text = ":red-badge[:material/thumb_down: Écartée]"
            if fb.reasons:
                text += " :gray[" + ", ".join(fb.reason_labels) + "]"
        if fb.comment:
            text += f" :gray[— « {fb.comment.replace('[', '(').replace(']', ')')} »]"
        st.markdown(text)
        if st.button("Retirer mon avis", icon=":material/undo:", key=f"undo-{uid}", type="tertiary"):
            delete_feedback(user_id, job_id)
            st.rerun()
        return

    with st.popover("J'aime", icon=":material/thumb_up:", key=f"like-{uid}"):
        with st.form(f"like-form-{uid}", border=False):
            comment = st.text_area(
                "Qu'est-ce qui te plaît ? (optionnel)",
                placeholder="ex. MLOps en production, équipe produit, full remote…",
                height=80,
            )
            if st.form_submit_button("Enregistrer", type="primary", icon=":material/check:"):
                set_feedback(user_id, job_id, LIKE, comment=comment, search_id=search_id)
                st.toast("Merci ! Les offres similaires seront favorisées.", icon=":material/thumb_up:")
                st.rerun()
    with st.popover("Pas pour moi", icon=":material/thumb_down:", key=f"dislike-{uid}"):
        with st.form(f"dislike-form-{uid}", border=False):
            reasons = st.pills(
                "Pourquoi ?", options=list(REASONS), format_func=REASONS.get, selection_mode="multi"
            )
            comment = st.text_area(
                "Précise (optionnel)",
                placeholder="ex. trop orienté BI, je cherche du ML en production",
                height=80,
            )
            ban = (
                st.checkbox(f"Ne plus me proposer d'offres de {r['company']}") if r.get("company") else False
            )
            if st.form_submit_button("Écarter cette offre", type="primary", icon=":material/block:"):
                set_feedback(user_id, job_id, DISLIKE, reasons, comment, search_id=search_id)
                if ban:
                    exclude_company(user_id, r["company"])
                st.toast("Offre écartée : elle ne te sera plus proposée.", icon=":material/thumb_down:")
                st.rerun()


def _applied_controls(user_id: int, search_id: int, r: dict, mark: AppliedMark | None) -> None:
    """« J'ai postulé » : la candidature se suit ensuite dans « Mes candidatures »."""
    job_id = r.get("job_id")
    if not job_id:
        return
    uid = f"{search_id}-{job_id}"
    if mark is not None:
        if st.button(
            f"Postulé le {mark.applied_on:%d/%m/%Y}",
            icon=":material/assignment_turned_in:",
            key=f"applied-{uid}",
            help="Ouvrir le suivi de cette candidature",
        ):
            st.session_state[APPLICATION_FOCUS_KEY] = mark.id
            go("applications")
        return
    with st.popover("J'ai postulé", icon=":material/send:", key=f"apply-{uid}"):
        with st.form(f"apply-form-{uid}", border=False):
            on = st.date_input("Date de candidature", date.today(), format="DD/MM/YYYY")
            comment = st.text_area(
                "Commentaire (optionnel)",
                placeholder="ex. candidature via le site de l'entreprise, contact : …",
                height=80,
            )
            if st.form_submit_button("Enregistrer", type="primary", icon=":material/check:"):
                try:
                    mark_applied(
                        user_id,
                        job_id,
                        applied_on=on,
                        comment=comment,
                        search_id=search_id,
                        score=r.get("final_score"),
                        summary=r.get("summary"),
                    )
                except ApplicationError as exc:
                    st.error(str(exc))
                else:
                    st.toast(
                        "Candidature enregistrée : suis-la dans « Mes candidatures ».", icon=":material/send:"
                    )
                    st.rerun()


def _result_card(
    rank: int, r: dict, user_id: int, search_id: int, fb: Feedback | None, mark: AppliedMark | None = None
) -> None:
    with st.container(border=True, key=f"gj_card_{search_id}_{rank}"):
        st.markdown(_card_header(rank, r), unsafe_allow_html=True)
        with st.expander("Analyse détaillée", icon=":material/insights:"):
            _analysis(r)
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.link_button("Voir l'offre", r["url"], icon=":material/open_in_new:", type="primary")
            _applied_controls(user_id, search_id, r, mark)
            _feedback_controls(user_id, search_id, r, fb)


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #


def render() -> None:
    user = require_user()
    st.html(CSS)
    st.markdown("## Mes recherches")

    searches = list_searches(user.id)
    if not searches:
        st.info("Aucune recherche pour l'instant.", icon=":material/manage_search:")
        st.page_link(page("search"), label="Lancer une recherche", icon=":material/rocket_launch:")
        return

    ids = [s.id for s in searches]
    selected = st.session_state.get("selected_search")
    index = ids.index(selected) if selected in ids else 0
    choice = st.selectbox(
        "Recherche", searches, index=index, format_func=_label, label_visibility="collapsed"
    )
    st.session_state["selected_search"] = choice.id
    s = get_search(user.id, choice.id)

    _search_header(user.id, s)

    if s.status in ("pending", "running"):
        _live_status(user.id, s.id)
        return
    if s.status == "failed":
        st.error(f"La recherche a échoué : {s.error}", icon=":material/error:")
        st.caption("Vérifie qu'Ollama est démarré et que les modèles sont téléchargés, puis relance.")
        return

    results = s.results or []
    _kpis(s, results)
    if not results:
        st.warning(
            "Aucune offre ne correspond. Élargis la période, les lieux ou retire des mots-clés exclus.",
            icon=":material/search_off:",
        )
        return

    feedback = feedback_map(user.id)
    applied = applied_map(user.id)
    rated = [r for r in results if r.get("job_id") in feedback]
    with st.container(border=True):
        c1, c2 = st.columns([3, 1], vertical_alignment="center")
        c1.markdown(
            ":material/tune: **Affine tes résultats** — indique les offres qui te plaisent ou non, et "
            "pourquoi : les prochaines recherches en tiennent compte."
        )
        c1.caption(
            f"{len(rated)} avis sur cette recherche · {len(feedback)} au total. "
            "« Réajuster » re-trie les offres déjà collectées, sans interroger les sites d’annonces."
        )
        if c2.button(
            "Réajuster avec mes avis",
            icon=":material/tune:",
            disabled=not feedback,
            use_container_width=True,
            help="Crée une nouvelle recherche avec les mêmes critères et tes avis actuels.",
        ):
            new_id = rerank_search(user.id, s.id)
            if new_id:
                get_runner().submit_search(new_id)
                st.session_state["selected_search"] = new_id
                st.rerun()

    # Filtres
    counts = {k: sum(1 for r in results if score_color(r["final_score"]) == k) for k in VERDICTS}
    f1, f2, f3 = st.columns([3, 2, 1.4], vertical_alignment="bottom")
    present = [k for k in VERDICTS if counts[k]]
    verdicts = f1.pills(
        "Pertinence",
        options=present,
        format_func=lambda k: f"{VERDICTS[k]} ({counts[k]})",
        selection_mode="multi",
        default=present,
        key=f"verdicts-{s.id}",
    )
    min_score = f2.slider("Score minimum", 0, 100, 0, step=5, key=f"min-score-{s.id}")
    hide_rejected = f3.toggle("Masquer les offres écartées", value=True)

    shown = [
        (rank, r)
        for rank, r in enumerate(results, 1)
        if r["final_score"] >= min_score
        and score_color(r["final_score"]) in (verdicts or [])
        and not (hide_rejected and (fb := feedback.get(r.get("job_id"))) and not fb.liked)
    ]
    st.caption(f"{len(shown)} offre(s) affichée(s) sur {len(results)}")
    for rank, r in shown:
        _result_card(rank, r, user.id, s.id, feedback.get(r.get("job_id")), applied.get(r.get("job_id")))
