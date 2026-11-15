"""Lancement d'une session de recherche personnalisée."""

from __future__ import annotations

from datetime import date

import streamlit as st

from getiajob.config import load_preferences
from getiajob.services.cvs import list_cvs
from getiajob.services.feedback import FeedbackSignals, feedback_signals, list_feedback
from getiajob.services.searches import SearchOptions, build_preferences, create_search
from getiajob.services.users import get_profile
from getiajob.web.layout import EXPERIENCE_LABELS, REMOTE_LABELS, freshness_input, parse_list, sources_input
from getiajob.web.state import get_runner, go, go_profile, require_user


def render() -> None:
    user = require_user()
    profile = get_profile(user.id)
    cvs = list_cvs(user.id)
    defaults = load_preferences()
    n_feedback = len(list_feedback(user.id))
    today = date.today()

    st.markdown("## Nouvelle recherche")
    st.caption("Les valeurs proposées viennent de ta fiche ; tu peux les ajuster pour cette recherche.")

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

    with st.form("search"):
        c1, c2 = st.columns(2)
        name = c1.text_input(
            "Nom de la recherche", placeholder=f"{profile.desired_job or 'Recherche'} — {today:%d/%m}"
        )
        cv = c2.selectbox("CV utilisé", options=cvs, format_func=lambda c: c.label)

        st.markdown("##### Fraîcheur et volume")
        c1, c2 = st.columns(2)
        with c1:
            max_age_days = freshness_input(
                "Fraîcheur des annonces",
                profile.max_offer_age_days or defaults.search.days,
                help="Ancienneté maximale des offres (valeur par défaut : celle de ta fiche).",
            )
        top_n = c2.slider("Nombre de résultats souhaités", 3, 50, defaults.ranking.top_n)

        st.markdown("##### Ciblage")
        keywords = st.text_input(
            "Requêtes de recherche (optionnel)",
            placeholder="Vide = générées par l'IA à partir de ton CV et de ta fiche",
            help="Séparées par des virgules. Si renseigné, remplace les requêtes générées.",
        )
        locations = st.text_input("Lieu(x)", value=", ".join(profile.locations or defaults.search.locations))
        sources = sources_input("Sources d'annonces", profile.sources, key="search_sources")
        c1, c2 = st.columns(2)
        remote = c1.multiselect(
            "Mode de travail",
            list(REMOTE_LABELS),
            default=profile.remote,
            placeholder="Choisir…",
            format_func=REMOTE_LABELS.get,
        )
        experience_levels = c2.multiselect(
            "Niveau d'expérience",
            list(EXPERIENCE_LABELS),
            default=profile.experience_levels,
            placeholder="Choisir…",
            format_func=EXPERIENCE_LABELS.get,
        )
        c1, c2, c3 = st.columns(3)
        must_have = c1.text_input("Mots-clés obligatoires", help="L'offre doit tous les contenir.")
        exclude_keywords = c2.text_input("Mots-clés exclus", value=", ".join(profile.exclude_keywords))
        exclude_companies = c3.text_input("Entreprises exclues", value=", ".join(profile.exclude_companies))

        use_feedback, feedback_llm = False, False
        if n_feedback:
            st.markdown("##### Mes avis")
            use_feedback = st.toggle(
                f"Tenir compte de mes avis ({n_feedback} offre(s))",
                value=True,
                help="Les offres écartées ne reviennent pas ; les offres proches de celles que tu as "
                "aimées gagnent des points, celles proches des offres écartées en perdent. "
                "Sans calcul supplémentaire (embeddings déjà en base).",
            )
            feedback_llm = st.toggle(
                "Transmettre aussi mes motifs et commentaires à l'IA",
                value=True,
                help="Un court résumé est ajouté aux attentes lues par l'IA (requêtes et notation). "
                "Plus précis, mais les offres déjà notées sont ré-évaluées quand tes avis ont changé.",
            )

        with st.expander("Options avancées"):
            c1, c2 = st.columns(2)
            max_queries = c1.slider("Nombre max de requêtes générées", 1, 8, defaults.search.max_queries)
            max_offers = c2.slider(
                "Offres max par requête et par lieu", 10, 150, defaults.search.max_offers_per_query, step=10
            )
            prefilter = c1.slider(
                "Offres évaluées par l'IA",
                5,
                100,
                defaults.ranking.prefilter_top_k,
                help="Les plus proches de ton CV après le tri sémantique. Plus = plus long.",
            )
            llm_weight = c2.slider(
                "Poids de la note IA dans le score final",
                0.0,
                1.0,
                defaults.ranking.llm_weight,
                0.05,
                help="Le reste vient de la similarité sémantique CV ↔ offre.",
            )
            skip_scrape = st.checkbox(
                "Ne pas interroger les sites d'annonces (trier uniquement les offres déjà en base)"
            )
            force_rank = st.checkbox("Ré-évaluer toutes les offres avec l'IA (ignorer le cache)")

        submitted = st.form_submit_button(
            "Lancer la recherche", type="primary", icon=":material/rocket_launch:"
        )

    if submitted and not sources and not skip_scrape:
        st.error("Choisis au moins une source d'annonces.")
    elif submitted and cv.analyzing:
        st.error("L'analyse de ce CV est encore en cours : relance la recherche dans quelques instants.")
    elif submitted:
        opts = SearchOptions(
            max_age_days=max_age_days,
            top_n=top_n,
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
        prefs = build_preferences(profile, opts, base=defaults, feedback=signals)
        search_id = create_search(
            user.id,
            cv.id,
            name or f"{profile.desired_job or 'Recherche'} — {today:%d/%m/%Y}",
            prefs,
            skip_scrape=skip_scrape,
            force_rank=force_rank,
            feedback_llm=use_feedback and feedback_llm,
        )
        get_runner().submit_search(search_id)
        st.session_state["selected_search"] = search_id
        go("results")
