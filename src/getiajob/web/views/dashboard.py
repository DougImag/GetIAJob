"""Tableau de bord : bandeau d'accueil, chiffres clés, prochaines étapes, meilleures offres, recherches."""

from __future__ import annotations

from datetime import datetime
from html import escape

import streamlit as st

from getiajob.db import SearchSession
from getiajob.services.applications import Application, list_applications, upcoming
from getiajob.services.cvs import CVSummary, list_cvs
from getiajob.services.feedback import list_feedback
from getiajob.services.searches import AWAITING, STATUS_LABELS, list_searches
from getiajob.services.users import ProfileData, get_profile
from getiajob.web.layout import FRESHNESS_LABELS, REMOTE_LABELS, score_color
from getiajob.web.state import go, go_profile, require_user
from getiajob.web.views.cv import ico
from getiajob.web.views.results import COLORS

CSS = """
<style>
  /* Bandeau d'accueil */
  .st-key-gj_dash_hero {
    border-radius: 22px; padding: 1.6rem 1.8rem; color: #fff; position: relative; overflow: hidden;
    background:
      radial-gradient(circle at 85% 20%, rgba(255, 255, 255, .18), transparent 40%),
      linear-gradient(120deg, var(--gj-primary), var(--gj-primary-2));
    box-shadow: 0 24px 50px -28px rgba(79, 70, 229, .9);
  }
  .gj-dh-hello { font-size: 1.75rem; font-weight: 800; letter-spacing: -.01em; line-height: 1.2; }
  .gj-dh-sub { margin-top: .35rem; opacity: .9; font-size: .98rem; }
  .gj-dh-chips { display: flex; flex-wrap: wrap; gap: .4rem; margin: .8rem 0 .9rem; }
  .gj-dh-chips span { display: inline-flex; align-items: center; gap: .3rem; font-size: .8rem;
    padding: .22rem .65rem; border-radius: 999px; background: rgba(255, 255, 255, .16);
    border: 1px solid rgba(255, 255, 255, .25); }
  .st-key-gj_dash_hero button { background: rgba(255, 255, 255, .14); color: #fff;
    border-color: rgba(255, 255, 255, .35); }
  .st-key-gj_dash_hero button:hover { background: rgba(255, 255, 255, .26); color: #fff;
    border-color: rgba(255, 255, 255, .6); }
  .st-key-gj_dash_hero .st-key-gj_dash_cta button { background: #fff; color: var(--gj-primary);
    border-color: #fff; font-weight: 700; }
  .st-key-gj_dash_hero .st-key-gj_dash_cta button:hover { background: rgba(255, 255, 255, .9);
    color: var(--gj-primary); }

  /* Chiffres clés */
  @media (max-width: 900px) { .gj-dk { grid-template-columns: repeat(2, 1fr) !important; } }
  @media (max-width: 520px) { .gj-dk { grid-template-columns: 1fr !important; } }
  .gj-dk { display: grid; grid-template-columns: repeat(3, 1fr); gap: .8rem;
    margin: 1.1rem 0 .4rem; }
  .gj-dk-c { display: flex; gap: .8rem; align-items: center; padding: .95rem 1rem; border-radius: 16px;
    background: var(--gj-surface); border: 1px solid var(--gj-line); }
  .gj-dk-i { flex-shrink: 0; width: 2.6rem; height: 2.6rem; border-radius: 12px; display: grid;
    place-items: center; color: #fff;
    background: linear-gradient(135deg, var(--gj-primary), var(--gj-primary-2)); }
  .gj-dk-i.green { background: linear-gradient(135deg, #16a34a, #0d9488); }
  .gj-dk-i.orange { background: linear-gradient(135deg, #f97316, #ec4899); }
  .gj-dk-i.violet { background: linear-gradient(135deg, #7c3aed, #4f46e5); }
  .gj-dk-i.amber { background: linear-gradient(135deg, #f59e0b, #ea580c); }
  .gj-dk-ring { --v: 0; --c: #6366f1; flex-shrink: 0; width: 2.6rem; height: 2.6rem; border-radius: 50%;
    display: grid; place-items: center; font-size: .7rem; font-weight: 800; color: var(--gj-primary);
    background: radial-gradient(var(--gj-surface) 58%, transparent 60%),
      conic-gradient(var(--c) calc(var(--v) * 1%), var(--gj-line) 0); }
  .gj-dk-v { font-size: 1.45rem; font-weight: 800; color: var(--gj-head); line-height: 1.1; }
  .gj-dk-l { font-size: .8rem; color: var(--gj-muted); }
  .gj-dk-s { font-size: .74rem; color: var(--gj-muted); margin-top: .1rem; }

  /* Consommation IA du mois */
  .gj-ai { display: flex; gap: .9rem; align-items: center; padding: .85rem 1rem; border-radius: 16px;
    background: var(--gj-surface); border: 1px solid var(--gj-line); margin: .2rem 0 .8rem; flex-wrap: wrap; }
  .gj-ai-main { flex: 1; min-width: 14rem; }
  .gj-ai-v { font-weight: 800; color: var(--gj-head); }
  .gj-ai-v b { font-size: 1.25rem; }
  .gj-ai-s { font-size: .76rem; color: var(--gj-muted); margin-top: .1rem; }
  .gj-ai-lim { flex: 1; min-width: 14rem; }
  .gj-ai-lim .gj-prog { margin: .35rem 0 .2rem; }
  .gj-ai-lim .gj-prog i { background: linear-gradient(90deg, var(--gj-primary), var(--gj-primary-2)); }
  .gj-ai-lim .gj-prog i.warn { background: linear-gradient(90deg, #f59e0b, #ea580c); }
  .gj-ai-lim .gj-prog i.full { background: linear-gradient(90deg, #ef4444, #be123c); }

  /* Blocs */
  [class*="st-key-gj_dash_box"] { border-radius: 18px !important; }
  .gj-db-t { display: flex; align-items: center; gap: .5rem; font-weight: 800; color: var(--gj-head);
    font-size: 1.05rem; }
  .gj-db-t svg { color: var(--gj-primary); }
  .gj-db-s { font-size: .82rem; color: var(--gj-muted); margin: .1rem 0 .4rem; }
  .gj-prog { height: .45rem; border-radius: 999px; background: var(--gj-surface-2); overflow: hidden;
    border: 1px solid var(--gj-line); margin: .3rem 0 .6rem; }
  .gj-prog i { display: block; height: 100%; border-radius: 999px;
    background: linear-gradient(90deg, #16a34a, #0d9488); }
  .gj-step { display: flex; align-items: center; gap: .65rem; padding: .35rem 0; color: var(--gj-text); }
  .gj-step-c { flex-shrink: 0; width: 1.7rem; height: 1.7rem; border-radius: 50%; display: grid;
    place-items: center; font-size: .78rem; font-weight: 800; border: 2px solid var(--gj-line);
    color: var(--gj-muted); }
  .gj-step.done { color: var(--gj-muted); }
  .gj-step.done .gj-step-l { text-decoration: line-through; }
  .gj-step.done .gj-step-c { background: #16a34a; border-color: #16a34a; color: #fff; }
  .gj-step-l { font-weight: 600; font-size: .92rem; }
  .gj-step-d { font-size: .78rem; color: var(--gj-muted); }

  /* Meilleures offres */
  .gj-to { display: flex; gap: .8rem; align-items: center; padding: .65rem .2rem;
    border-bottom: 1px dashed var(--gj-line); }
  .gj-to:last-child { border-bottom: none; }
  .gj-to-ring { --v: 0; --c: #16a34a; flex-shrink: 0; width: 2.7rem; height: 2.7rem; border-radius: 50%;
    display: grid; place-items: center; font-weight: 800; font-size: .85rem; color: var(--gj-head);
    background: radial-gradient(var(--gj-surface) 58%, transparent 60%),
      conic-gradient(var(--c) calc(var(--v) * 1%), var(--gj-line) 0); }
  .gj-to-main { min-width: 0; }
  .gj-to-t a { font-weight: 700; color: var(--gj-head) !important; text-decoration: none; }
  .gj-to-t a:hover { color: var(--gj-primary) !important; text-decoration: underline; }
  .gj-to-m { display: flex; flex-wrap: wrap; gap: .1rem .8rem; font-size: .78rem; color: var(--gj-muted); }
  .gj-to-m span { display: inline-flex; align-items: center; gap: .25rem; }

  /* Recherches récentes */
  .gj-rs-n { font-weight: 700; color: var(--gj-head); }
  .gj-rs-m { display: flex; flex-wrap: wrap; gap: .1rem .8rem; font-size: .78rem; color: var(--gj-muted);
    margin-top: .1rem; }
  .gj-rs-m span { display: inline-flex; align-items: center; gap: .25rem; }
  .gj-badge { font-size: .7rem; font-weight: 700; padding: .08rem .5rem; border-radius: 999px;
    margin-left: .4rem; vertical-align: middle; }
  .gj-badge.done { background: rgba(34, 197, 94, .14); color: #15803d; }
  .gj-badge.running, .gj-badge.pending { background: rgba(99, 102, 241, .14); color: var(--gj-primary); }
  .gj-badge.failed { background: rgba(239, 68, 68, .14); color: #b91c1c; }
  .gj-badge.cancelled { background: rgba(107, 114, 128, .14); color: #4b5563; }
  .gj-badge.awaiting { background: rgba(234, 88, 12, .14); color: #c2410c; }
  .gj-empty { text-align: center; padding: 1.2rem .5rem; color: var(--gj-muted); font-size: .9rem; }
  .gj-empty svg { color: var(--gj-primary); opacity: .6; margin-bottom: .3rem; }
</style>
"""


def _greeting() -> str:
    hour = datetime.now().hour
    return "Bonsoir" if hour >= 18 or hour < 5 else "Bonjour"


def _hero(name: str, profile: ProfileData) -> None:
    with st.container(key="gj_dash_hero"):
        if profile.desired_job:
            sub = f"Objectif : <b>{escape(profile.desired_job)}</b>. Voici où en est ta recherche."
        else:
            sub = "Précise le poste que tu cherches pour que l'IA te propose les bonnes offres."
        chips = []
        if profile.locations:
            chips.append(f"{ico('map-pin', 13)}{escape(', '.join(profile.locations))}")
        if profile.remote:
            chips.append(
                f"{ico('house', 13)}{escape(' / '.join(REMOTE_LABELS.get(r, r) for r in profile.remote))}"
            )
        if profile.contract_types:
            chips.append(f"{ico('file', 13)}{escape(', '.join(profile.contract_types))}")
        fresh = FRESHNESS_LABELS.get(profile.max_offer_age_days, "1 semaine")
        chips.append(f"{ico('clock', 13)}Annonces de moins de {fresh}")
        st.markdown(
            f'<div class="gj-dh-hello">{_greeting()} {escape(name)} !</div><div class="gj-dh-sub">{sub}</div>'
            f'<div class="gj-dh-chips">{"".join(f"<span>{c}</span>" for c in chips)}</div>',
            unsafe_allow_html=True,
        )
        with st.container(horizontal=True, gap="small"):
            with st.container(key="gj_dash_cta", width="content"):
                if st.button("Nouvelle recherche", icon=":material/manage_search:"):
                    go("search")
            if st.button("Mes recherches", icon=":material/work_history:"):
                go("results")
            if st.button("Ma fiche", icon=":material/tune:"):
                go_profile("fiche")


def _kpi(icon_html: str, value: str, label: str, sub: str = "") -> str:
    sub_html = f'<div class="gj-dk-s">{escape(sub)}</div>' if sub else ""
    return (
        f'<div class="gj-dk-c">{icon_html}<div><div class="gj-dk-v">{escape(value)}</div>'
        f'<div class="gj-dk-l">{escape(label)}</div>{sub_html}</div></div>'
    )


def _kpis(
    completion: int,
    cvs: list[CVSummary],
    searches: list[SearchSession],
    best,
    liked: int,
    disliked: int,
    apps: list[Application],
):
    ring_color = "#16a34a" if completion == 100 else "#ea580c" if completion < 50 else "#6366f1"
    cv = cvs[0] if cvs else None
    if cv is None:
        cv_sub = "aucun CV importé"
    elif cv.analyzing:
        cv_sub = "analyse en cours…"
    elif cv.profile is None or cv.profile.is_outdated:
        cv_sub = "analyse à lancer"
    else:
        cv_sub = f"analysé le {cv.created_at:%d/%m/%Y}"
    running = sum(1 for s in searches if s.status in ("pending", "running"))
    cards = [
        _kpi(
            f'<div class="gj-dk-ring" style="--v:{completion};--c:{ring_color}">{ico("sliders", 15)}</div>',
            f"{completion} %",
            "Fiche de recherche",
            "complète" if completion == 100 else "à compléter",
        ),
        _kpi(f'<div class="gj-dk-i green">{ico("file", 20)}</div>', str(len(cvs)), "CV", cv_sub),
        _kpi(
            f'<div class="gj-dk-i">{ico("search", 20)}</div>',
            str(len(searches)),
            "Recherches",
            f"dont {running} en cours" if running else "",
        ),
        _kpi(
            f'<div class="gj-dk-i amber">{ico("award", 20)}</div>',
            f"{best:.0f}/100" if best is not None else "—",
            "Meilleur score",
            "dernière recherche terminée",
        ),
        _kpi(
            f'<div class="gj-dk-i orange">{ico("heart", 20)}</div>',
            str(liked + disliked),
            "Avis donnés",
            f"{liked} aimée(s) · {disliked} écartée(s)" if liked + disliked else "affinent les recherches",
        ),
        _kpi(
            f'<div class="gj-dk-i violet">{ico("briefcase", 20)}</div>',
            str(sum(1 for a in apps if a.active)),
            "Candidatures en cours",
            _next_step_text(apps) or (f"{len(apps)} au total" if apps else "« J'ai postulé » sur une offre"),
        ),
    ]
    st.markdown(f'<div class="gj-dk">{"".join(cards)}</div>', unsafe_allow_html=True)


def _limit_html(title: str, used: int, limit: int, extra: str = "") -> str:
    from getiajob.web.views.admin_format import tokens_label

    pct = min(100, round(100 * used / limit))
    tone = "full" if used >= limit else "warn" if pct >= 80 else ""
    left = (
        "<b>limite atteinte</b> : l'IA reprendra le 1er du mois prochain"
        if tone == "full"
        else f"{tokens_label(max(0, limit - used))} restants"
    )
    return (
        f'<div class="gj-ai-lim"><div class="gj-ai-s">{title} : <b>{tokens_label(limit)}</b> tokens</div>'
        f'<div class="gj-prog"><i class="{tone}" style="width:{pct}%"></i></div>'
        f'<div class="gj-ai-s">{tokens_label(used)} utilisés ({pct} %) · {left}{extra}</div></div>'
    )


def _ai_usage(user_id: int) -> None:
    """Tokens consommés ce mois-ci par l'utilisateur, et sa limite mensuelle (personnelle, sinon globale)."""
    from getiajob.services import llm_settings
    from getiajob.services.usage import month_tokens
    from getiajob.web.views.admin_format import tokens_label

    mine = month_tokens(user_id)
    month = MONTHS[datetime.now().month - 1]
    lim_html = ""
    if (personal := llm_settings.user_limit(user_id)) is not None:
        lim_html = _limit_html("Ta limite mensuelle", mine, personal)
    else:
        limit = llm_settings.token_limit()
        if limit.enabled and limit.period == "month":
            extra = " · LLM local (Ollama) non compté" if not limit.include_local else ""
            lim_html = _limit_html(
                "Limite mensuelle partagée par tous les utilisateurs",
                llm_settings.used_tokens(limit),
                limit.tokens,
                extra,
            )
    st.markdown(
        f'<div class="gj-ai"><div class="gj-dk-i violet">{ico("sparkles", 20)}</div>'
        f'<div class="gj-ai-main"><div class="gj-ai-v"><b>{tokens_label(mine)}</b> tokens</div>'
        f'<div class="gj-ai-s">consommés par l\'IA pour toi en {month} (analyses de CV, évaluation des '
        f"offres, alertes e-mail)</div></div>{lim_html}</div>",
        unsafe_allow_html=True,
    )


MONTHS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]  # fmt: skip


def _next_step_text(apps: list[Application]) -> str:
    soon = upcoming(apps)
    if not soon:
        return ""
    a = soon[0]
    return f"prochaine étape le {a.next_step_on:%d/%m} · {a.company or a.title}"


def _box_title(icon_name: str, title: str, subtitle: str = "") -> None:
    sub = f'<div class="gj-db-s">{escape(subtitle)}</div>' if subtitle else ""
    st.markdown(
        f'<div class="gj-db-t">{ico(icon_name, 18)}{escape(title)}</div>{sub}', unsafe_allow_html=True
    )


def _steps(cvs: list[CVSummary], completion: int, missing: list[str], searches: list[SearchSession]) -> None:
    cv_ready = any(c.profile is not None and not c.profile.is_outdated for c in cvs)
    steps = [
        (bool(cvs), "Charger mon CV", "PDF, Word, Markdown ou texte", lambda: go_profile("cv")),
        (cv_ready, "Faire analyser mon CV", "L'IA en extrait ton profil détaillé", lambda: go_profile("cv")),
        (
            completion == 100,
            "Compléter ma fiche de recherche",
            ("Manque : " + ", ".join(missing)) if missing else "Poste, lieux, conditions",
            lambda: go_profile("fiche"),
        ),
        (
            bool(searches),
            "Lancer ma première recherche",
            "Les offres récentes, notées par l'IA",
            lambda: go("search"),
        ),
    ]
    n_done = sum(done for done, *_ in steps)
    with st.container(border=True, key="gj_dash_box_steps"):
        _box_title("check", "Prochaines étapes", f"{n_done} sur {len(steps)} terminée(s)")
        st.markdown(
            f'<div class="gj-prog"><i style="width:{round(100 * n_done / len(steps))}%"></i></div>',
            unsafe_allow_html=True,
        )
        for i, (done, label, detail, action) in enumerate(steps, start=1):
            c1, c2 = st.columns([5, 2], vertical_alignment="center")
            mark = ico("check", 14) if done else str(i)
            c1.markdown(
                f'<div class="gj-step{" done" if done else ""}"><div class="gj-step-c">{mark}</div><div>'
                f'<div class="gj-step-l">{escape(label)}</div><div class="gj-step-d">{escape(detail)}</div>'
                "</div></div>",
                unsafe_allow_html=True,
            )
            if not done and c2.button(
                "Y aller", key=f"step-{i}", icon=":material/arrow_forward:", width="stretch"
            ):
                action()


def _top_offers(last_done: SearchSession | None) -> None:
    with st.container(border=True, key="gj_dash_box_top"):
        if not (last_done and last_done.results):
            _box_title("award", "Meilleures offres")
            st.markdown(
                f'<div class="gj-empty">{ico("search", 30)}<br>Lance une recherche pour voir ici '
                "les offres qui te correspondent le mieux.</div>",
                unsafe_allow_html=True,
            )
            return
        _box_title("award", "Meilleures offres", f"Dernière recherche : {last_done.name}")
        rows = []
        for r in last_done.results[:4]:
            score = r["final_score"]
            meta = "".join(
                f"<span>{ico(i, 12)}{escape(v)}</span>"
                for i, v in (("building", r.get("company")), ("map-pin", r.get("location")))
                if v
            )
            rows.append(
                f'<div class="gj-to"><div class="gj-to-ring" style="--v:{score:.0f};'
                f'--c:{COLORS[score_color(score)]}">{score:.0f}</div><div class="gj-to-main">'
                f'<div class="gj-to-t"><a href="{escape(r["url"], quote=True)}" target="_blank" '
                'rel="noopener">'
                f'{escape(r["title"])}</a></div><div class="gj-to-m">{meta}</div></div></div>'
            )
        st.markdown("".join(rows), unsafe_allow_html=True)
        if st.button(
            "Voir toute l'analyse", icon=":material/arrow_forward:", key="dash-top", type="tertiary"
        ):
            st.session_state["selected_search"] = last_done.id
            go("results")


def _recent_searches(searches: list[SearchSession]) -> None:
    if not searches:
        return
    with st.container(border=True, key="gj_dash_box_recent"):
        _box_title("clock", "Dernières recherches")
        for s in searches[:5]:
            c1, c2 = st.columns([6, 1], vertical_alignment="center")
            results = s.results or []
            meta = [f"<span>{ico('calendar', 12)}{s.created_at:%d/%m/%Y à %H:%M}</span>"]
            if s.n_offers:
                meta.append(f"<span>{ico('list', 12)}{s.n_offers} offres collectées</span>")
            if results:
                meta.append(f"<span>{ico('award', 12)}meilleur score {results[0]['final_score']:.0f}</span>")
            c1.markdown(
                f'<div class="gj-rs-n">{escape(s.name)}<span class="gj-badge {s.status}">'
                f"{escape(STATUS_LABELS.get(s.status, s.status))}</span></div>"
                f'<div class="gj-rs-m">{"".join(meta)}</div>',
                unsafe_allow_html=True,
            )
            if c2.button("Voir", key=f"see-{s.id}", width="stretch"):
                st.session_state["selected_search"] = s.id
                go("results")


def render() -> None:
    user = require_user()
    st.html(CSS)
    profile = get_profile(user.id)
    cvs = list_cvs(user.id)
    searches = list_searches(user.id)
    feedback = list_feedback(user.id)
    completion, missing = profile.completion()
    last_done = next((s for s in searches if s.status == "done" and s.results), None)
    best = last_done.results[0]["final_score"] if last_done else None
    liked = sum(1 for f in feedback if f.liked)
    apps = list_applications(user.id)

    _hero(user.display_name, profile)
    _kpis(completion, cvs, searches, best, liked, len(feedback) - liked, apps)
    _ai_usage(user.id)

    if analyzing := [cv for cv in cvs if cv.analyzing]:
        st.info(
            f"Analyse de « {analyzing[0].file_name} » en cours en arrière-plan. "
            "Tu peux continuer à naviguer, le profil sera prêt dans quelques minutes.",
            icon=":material/hourglass_top:",
        )
    if awaiting := [s for s in searches if s.status == AWAITING]:
        st.warning(
            f"Recherche « {awaiting[0].name} » : offres collectées, l'évaluation par l'IA attend ta "
            "confirmation (coût estimé affiché dans « Mes recherches »).",
            icon=":material/pending_actions:",
        )
    if running := [s for s in searches if s.status in ("pending", "running")]:
        st.info(
            f"Recherche « {running[0].name} » {STATUS_LABELS[running[0].status].lower()} : "
            "les résultats apparaîtront dans « Mes recherches ».",
            icon=":material/travel_explore:",
        )

    left, right = st.columns([1, 1], gap="medium")
    with left:
        _steps(cvs, completion, missing, searches)
    with right:
        _top_offers(last_done)
    _recent_searches(searches)
