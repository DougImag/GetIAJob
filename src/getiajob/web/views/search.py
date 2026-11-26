"""Lancement d'une session de recherche personnalisée."""

from __future__ import annotations

from datetime import date

import streamlit as st

from getiajob.config import load_preferences
from getiajob.services import estimate
from getiajob.services.cvs import CVSummary, list_cvs
from getiajob.services.feedback import FeedbackSignals, feedback_signals, list_feedback
from getiajob.services.searches import (
    SearchOptions,
    build_preferences,
    create_search,
    load_search_form,
    reset_search_form,
    save_search_form,
)
from getiajob.services.seen import seen_ids
from getiajob.services.users import get_profile
from getiajob.web.layout import EXPERIENCE_LABELS, REMOTE_LABELS, freshness_input, parse_list, sources_input
from getiajob.web.state import get_runner, go, go_profile, is_read_only, require_user
from getiajob.web.views.admin_format import (
    budget_status,
    estimate_label,
    estimate_note,
    show_budget,
    tokens_label,
)


def _clamp(value, low, high, default):
    """Valeur mémorisée ramenée dans les bornes du curseur (défaut si absente ou invalide)."""
    try:
        return type(default)(min(high, max(low, value)))
    except (TypeError, ValueError):
        return default


def _estimate_caption(cv: CVSummary, max_offers: int, plan_queries: bool, user_id: int | None = None) -> bool:
    """Tokens estimés de la recherche (au plus `max_offers` offres évaluées par l'IA), et budget restant.

    Renvoie False si la limite de tokens est atteinte (lancement impossible)."""
    if cv.profile is None:
        return True
    est = estimate.search(cv.profile.to_text(), max_offers, plan_queries, estimate.assessed_offers(cv.id))
    cached = (
        f" Les {est.already_assessed} offre(s) déjà évaluées pour ce CV ne sont pas réévaluées si elles "
        "reviennent (sauf « Ré-évaluer toutes les offres »)."
        if est.already_assessed
        else ""
    )
    st.caption(
        f":material/token: **Estimation IA** : ~{tokens_label(est.per_offer.total)} tokens par offre "
        f"évaluée, soit au plus {estimate_label(est.maximum)} pour {max_offers} offres "
        f"(« Offres évaluées par l'IA », options avancées){estimate_note(est.maximum)}.{cached}",
        help="Calculée pour le CV et le nombre d'offres affichés à l'ouverture du formulaire : un autre "
        "choix change le total en proportion. Tokens envoyés calculés d'après ton profil et la taille "
        "moyenne des offres ; tokens générés d'après les dernières évaluations du même modèle.",
    )
    msg, level = budget_status(est.maximum.total, user_id)
    if msg:
        show_budget(msg, level)
    return level != "block"


def render() -> None:
    user = require_user()
    profile = get_profile(user.id)
    cvs = list_cvs(user.id)
    defaults = load_preferences()
    n_feedback = len(list_feedback(user.id))
    seen = seen_ids(user.id)
    today = date.today()
    saved = load_search_form(user.id)

    st.markdown("## Nouvelle recherche")

    if not cvs:
        st.info("Importe d'abord ton CV pour lancer une recherche.")
        if st.button("Importer mon CV", icon=":material/upload:"):
            go_profile("cv")
        return
    if not profile.desired_job:
        st.warning("Ta fiche n'indique pas de poste recherché : l'IA se basera uniquement sur ton CV.")
        if st.button("Compléter ma fiche de recherche", icon=":material/tune:"):
            go_profile("fiche")
    if any(c.analyzing for c in cvs):
        st.info(
            "Un CV est en cours d'analyse : attends la fin avant de lancer une recherche avec ce CV.",
            icon=":material/hourglass_top:",
        )

    if saved:
        c1, c2 = st.columns([4, 1.4], vertical_alignment="center")
        c1.caption(
            ":material/history: Options reprises de ta dernière recherche "
            "(les champs issus de ta fiche reprennent ses valeurs quand tu l'enregistres)."
        )
        if c2.button(
            "Valeurs par défaut",
            icon=":material/restart_alt:",
            key="search_reset",
            disabled=is_read_only(),
            width="stretch",
        ):
            reset_search_form(user.id)
            st.rerun()

    def text(key: str, fallback: list[str]) -> str:
        return ", ".join(saved.get(key, fallback) or [])

    with st.form("search"):
        c1, c2 = st.columns(2)
        name = c1.text_input(
            "Nom de la recherche", placeholder=f"{profile.desired_job or 'Recherche'} — {today:%d/%m}"
        )
        cv_index = next((i for i, c in enumerate(cvs) if c.id == saved.get("cv_id")), 0)
        cv = c2.selectbox("CV utilisé", options=cvs, index=cv_index, format_func=lambda c: c.label)

        st.markdown("##### Fraîcheur et volume")
        c1, c2, c3 = st.columns(3)
        with c1:
            max_age_days = freshness_input(
                "Fraîcheur des annonces",
                saved.get("max_age_days") or profile.max_offer_age_days or defaults.search.days,
                help="Ancienneté maximale des offres (valeur par défaut : celle de ta fiche).",
            )
        min_score = c2.slider(
            "Score de matching minimum",
            0,
            100,
            _clamp(saved.get("min_score"), 0, 100, int(defaults.ranking.min_score)),
            step=5,
            help="Score final CV ↔ offre (sur 100) en dessous duquel une offre n'est pas retenue.",
        )
        top_n = c3.slider(
            "Nombre de résultats souhaités", 3, 50, _clamp(saved.get("top_n"), 3, 50, defaults.ranking.top_n)
        )

        st.markdown("##### Ciblage")
        c1, c2 = st.columns(2)
        keywords = c1.text_input(
            "Requêtes de recherche (optionnel)",
            value=text("keywords", []),
            placeholder="Vide = générées par l'IA à partir de ton CV et de ta fiche",
            help="Séparées par des virgules. Si renseigné, remplace les requêtes générées.",
        )
        locations = c2.text_input(
            "Lieu(x)", value=text("locations", profile.locations or defaults.search.locations)
        )
        sources = sources_input(
            "Sources d'annonces", saved.get("sources", profile.sources), key="search_sources"
        )
        c1, c2 = st.columns(2)
        remote = c1.multiselect(
            "Mode de travail",
            list(REMOTE_LABELS),
            default=[r for r in saved.get("remote", profile.remote) if r in REMOTE_LABELS],
            placeholder="Choisir…",
            format_func=REMOTE_LABELS.get,
        )
        experience_levels = c2.multiselect(
            "Niveau d'expérience",
            list(EXPERIENCE_LABELS),
            default=[
                e for e in saved.get("experience_levels", profile.experience_levels) if e in EXPERIENCE_LABELS
            ],
            placeholder="Choisir…",
            format_func=EXPERIENCE_LABELS.get,
        )
        c1, c2, c3 = st.columns(3)
        must_have = c1.text_input(
            "Mots-clés obligatoires", text("must_have_keywords", []), help="L'offre doit tous les contenir."
        )
        exclude_keywords = c2.text_input(
            "Mots-clés exclus", value=text("exclude_keywords", profile.exclude_keywords)
        )
        exclude_companies = c3.text_input(
            "Entreprises exclues", value=text("exclude_companies", profile.exclude_companies)
        )

        use_feedback, feedback_llm = False, False
        hide_seen = saved.get("hide_seen", True)
        if n_feedback or seen:
            st.markdown("##### Mes avis")
        if n_feedback:
            use_feedback = st.toggle(
                f"Tenir compte de mes avis ({n_feedback} offre(s))",
                value=saved.get("use_feedback", True),
                help="Les offres écartées ne reviennent pas ; les offres proches de celles que tu as "
                "aimées gagnent des points, celles proches des offres écartées en perdent. "
                "Sans calcul supplémentaire (embeddings déjà en base).",
            )
            feedback_llm = st.toggle(
                "Transmettre aussi mes motifs et commentaires à l'IA",
                value=saved.get("feedback_llm", True),
                help="Un court résumé est ajouté aux attentes lues par l'IA (requêtes et notation). "
                "Plus précis, mais les offres déjà notées sont ré-évaluées quand tes avis ont changé.",
            )

        if seen:
            hide_seen = st.toggle(
                f"Ne pas reproposer les offres déjà consultées ({len(seen)} offre(s))",
                value=saved.get("hide_seen", True),
                help="Les offres ouvertes avec « Voir l'offre » (ou leur titre) dans une recherche "
                "précédente sont écartées : seules des offres que tu n'as pas encore vues sont classées.",
            )

        with st.expander("Options avancées"):
            c1, c2 = st.columns(2)
            max_queries = c1.slider(
                "Nombre max de requêtes générées",
                1,
                8,
                _clamp(saved.get("max_queries"), 1, 8, defaults.search.max_queries),
            )
            max_offers = c2.slider(
                "Offres max par requête et par lieu",
                10,
                150,
                _clamp(saved.get("max_offers_per_query"), 10, 150, defaults.search.max_offers_per_query),
                step=10,
            )
            prefilter = c1.slider(
                "Offres évaluées par l'IA",
                5,
                100,
                _clamp(saved.get("prefilter_top_k"), 5, 100, defaults.ranking.prefilter_top_k),
                help="Les plus proches de ton CV après le tri sémantique. Plus = plus long.",
            )
            llm_weight = c2.slider(
                "Poids de la note IA dans le score final",
                0.0,
                1.0,
                _clamp(saved.get("llm_weight"), 0.0, 1.0, defaults.ranking.llm_weight),
                0.05,
                help="Le reste vient de la similarité sémantique CV ↔ offre.",
            )
            skip_scrape = st.checkbox(
                "Ne pas interroger les sites d'annonces (trier uniquement les offres déjà en base)"
            )
            force_rank = st.checkbox("Ré-évaluer toutes les offres avec l'IA (ignorer le cache)")

        confirm_llm = st.toggle(
            "Me montrer le coût estimé et demander confirmation avant l'évaluation par l'IA",
            value=saved.get("confirm_llm", True),
            help="La recherche s'arrête une fois les offres collectées : tu vois combien d'offres l'IA "
            "va évaluer et le nombre de tokens précis, puis tu confirmes (ou réduis le nombre d'offres). "
            "Pas de pause si toutes les offres ont déjà été évaluées.",
        )
        can_launch = _estimate_caption(
            cvs[cv_index],
            _clamp(saved.get("prefilter_top_k"), 5, 100, defaults.ranking.prefilter_top_k),
            plan_queries=not saved.get("keywords"),
            user_id=user.id,
        )
        submitted = st.form_submit_button(
            "Lancer la recherche", type="primary", icon=":material/rocket_launch:", disabled=not can_launch
        )

    if submitted and not sources and not skip_scrape:
        st.error("Choisis au moins une source d'annonces.")
    elif submitted and cv.analyzing:
        st.error("L'analyse de ce CV est encore en cours : relance la recherche dans quelques instants.")
    elif submitted:
        opts = SearchOptions(
            max_age_days=max_age_days,
            top_n=top_n,
            min_score=min_score,
            keywords=parse_list(keywords),
            locations=parse_list(locations),
            remote=remote,
            experience_levels=experience_levels,
            max_queries=max_queries,
            max_offers_per_query=max_offers,
            prefilter_top_k=prefilter,
            llm_weight=llm_weight,
            must_have_keywords=parse_list(must_have),
            exclude_keywords=parse_list(exclude_keywords),
            exclude_companies=parse_list(exclude_companies),
            sources=sources,
        )
        signals = feedback_signals(user.id, with_digest=feedback_llm) if use_feedback else FeedbackSignals()
        prefs = build_preferences(
            profile, opts, base=defaults, feedback=signals, seen_job_ids=seen if hide_seen else None
        )
        search_id = create_search(
            user.id,
            cv.id,
            name or f"{profile.desired_job or 'Recherche'} — {today:%d/%m/%Y}",
            prefs,
            skip_scrape=skip_scrape,
            force_rank=force_rank,
            feedback_llm=use_feedback and feedback_llm,
            hide_seen=bool(seen) and hide_seen,
            confirm=confirm_llm,
        )
        save_search_form(
            user.id,
            opts,
            cv_id=cv.id,
            use_feedback=use_feedback if n_feedback else saved.get("use_feedback", True),
            feedback_llm=feedback_llm if n_feedback else saved.get("feedback_llm", True),
            hide_seen=hide_seen,
            confirm_llm=confirm_llm,
        )
        get_runner().submit_search(search_id)
        st.session_state["selected_search"] = search_id
        go("results")
