"""Sessions de recherche : construction des préférences, exécution en tâche de fond, historique."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from getiajob.config import Expectations, Preferences, RankingPreferences, load_preferences
from getiajob.db import SearchSession, UserProfile, session_scope
from getiajob.services import usage
from getiajob.services.cancellation import SearchCancelled, check_cancelled, search_context
from getiajob.services.cvs import owns_cv
from getiajob.services.feedback import FeedbackSignals, feedback_signals
from getiajob.services.seen import seen_ids
from getiajob.services.users import ProfileData

log = logging.getLogger(__name__)

STEP_LABELS = {
    "analyze_cv": "Analyse du CV",
    "scrape_jobs": "Collecte des offres",
    "rank_jobs": "Tri et évaluation des offres",
}
STATUS_LABELS = {
    "pending": "En attente",
    "awaiting": "À confirmer",
    "running": "En cours",
    "done": "Terminée",
    "failed": "Échec",
    "cancelled": "Arrêtée",
}
STOPPED = "Arrêtée à ta demande."
MAX_DAYS = 30


@dataclass
class SearchOptions:
    """Paramètres saisis dans le formulaire « Nouvelle recherche »."""

    max_age_days: int = 7  # fraîcheur des annonces
    top_n: int = 20
    min_score: int = 60  # seuil du score de matching CV ↔ offre
    keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    remote: list[str] = field(default_factory=list)
    experience_levels: list[str] = field(default_factory=list)
    max_queries: int = 4
    max_offers_per_query: int = 50
    prefilter_top_k: int = 30
    llm_weight: float = 0.7
    must_have_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    exclude_companies: list[str] = field(default_factory=list)
    sources: list[str] | None = None  # None = celles de la fiche


# Valeurs du formulaire mémorisées en plus des SearchOptions. « Ne pas interroger les sites » et
# « Ré-évaluer toutes les offres » n'en font pas partie : ce sont des actions ponctuelles.
FORM_EXTRAS = ("cv_id", "use_feedback", "feedback_llm", "hide_seen", "confirm_llm")
# Recherche en pause après la collecte : l'utilisateur confirme (ou non) l'évaluation par le LLM
AWAITING = "awaiting"


def load_search_form(user_id: int) -> dict:
    """Dernières valeurs du formulaire « Nouvelle recherche » ({} si aucune)."""
    with session_scope() as session:
        row = session.get(UserProfile, user_id)
        return dict(row.search_options or {}) if row else {}


def save_search_form(user_id: int, opts: SearchOptions, **extras) -> None:
    """Mémorise les valeurs du formulaire pour les proposer à la recherche suivante."""
    values = asdict(opts) | {k: v for k, v in extras.items() if k in FORM_EXTRAS}
    with session_scope() as session:
        row = session.get(UserProfile, user_id) or UserProfile(user_id=user_id)
        row.search_options = values
        session.add(row)


def reset_search_form(user_id: int) -> None:
    with session_scope() as session:
        if row := session.get(UserProfile, user_id):
            row.search_options = None


def clamp_days(days: int) -> int:
    """Fraîcheur des annonces bornée à ce que les sources acceptent (1 à 30 jours)."""
    return max(1, min(MAX_DAYS, int(days)))


def build_preferences(
    profile: ProfileData,
    opts: SearchOptions,
    base: Preferences | None = None,
    feedback: FeedbackSignals | None = None,
    seen_job_ids: list[int] | None = None,
) -> Preferences:
    """Fusionne config YAML (valeurs par défaut) < fiche utilisateur < options de la recherche.

    `feedback` : avis de l'utilisateur sur des offres précédentes, figés dans la recherche.
    `seen_job_ids` : offres déjà consultées, exclues de la recherche.
    """
    feedback = feedback or FeedbackSignals()
    base = base or load_preferences()
    search = base.search.model_copy(
        update={
            "keywords": opts.keywords,
            "locations": opts.locations or profile.locations or base.search.locations,
            "remote": opts.remote,
            "experience_levels": opts.experience_levels,
            "days": clamp_days(opts.max_age_days),
            "max_queries": opts.max_queries,
            "max_offers_per_query": opts.max_offers_per_query,
            "sources": opts.sources if opts.sources is not None else profile.sources or base.search.sources,
        }
    )
    expectations = Expectations(
        desired_job=profile.desired_job,
        description=profile.expectations,
        min_salary=profile.min_salary,
        contract_types=profile.contract_types,
        must_have_keywords=opts.must_have_keywords,
        exclude_keywords=opts.exclude_keywords,
        exclude_companies=opts.exclude_companies,
        feedback=feedback.digest,
    )
    ranking = RankingPreferences(
        prefilter_top_k=max(opts.prefilter_top_k, opts.top_n),
        top_n=opts.top_n,
        min_score=opts.min_score,
        llm_weight=opts.llm_weight,
        hidden_job_ids=feedback.hidden_job_ids,
        liked_job_ids=feedback.liked_job_ids,
        disliked_job_ids=feedback.disliked_job_ids,
        seen_job_ids=seen_job_ids or [],
    )
    return Preferences(search=search, expectations=expectations, ranking=ranking)


def create_search(
    user_id: int,
    cv_id: int,
    name: str,
    prefs: Preferences,
    *,
    skip_scrape: bool = False,
    force_rank: bool = False,
    feedback_llm: bool = True,
    hide_seen: bool = False,
    confirm: bool = False,
) -> int:
    """`confirm` : s'arrêter après la collecte pour faire confirmer l'évaluation par le LLM (coût estimé)."""
    if not owns_cv(user_id, cv_id):
        raise PermissionError("Ce CV n'appartient pas à l'utilisateur.")
    with session_scope() as session:
        search = SearchSession(
            user_id=user_id,
            cv_id=cv_id,
            name=name.strip() or f"Recherche du {datetime.now():%d/%m/%Y %H:%M}",
            params={
                "preferences": prefs.model_dump(mode="json"),
                "skip_scrape": skip_scrape,
                "force_rank": force_rank,
                "feedback_llm": feedback_llm,  # résumé des avis transmis au LLM ?
                "hide_seen": hide_seen,  # offres déjà consultées exclues ?
                "confirm": confirm,  # confirmation avant l'évaluation par le LLM ?
            },
            status="pending",
        )
        session.add(search)
        session.flush()
        return search.id


def rerank_search(user_id: int, search_id: int) -> int | None:
    """Nouvelle recherche qui re-trie les offres déjà en base avec les avis actuels (sans nouvelle collecte).

    Les paramètres d'origine sont conservés ; seuls les avis sont mis à jour. L'IA n'évalue que
    les offres nouvelles dans la pré-sélection, ou toutes si le résumé des avis a changé.
    """
    with session_scope() as session:
        source = session.scalar(
            select(SearchSession).where(SearchSession.id == search_id, SearchSession.user_id == user_id)
        )
        if source is None or source.cv_id is None:
            return None
        prefs = Preferences.model_validate(source.params["preferences"])
        name, cv_id = source.name, source.cv_id
        feedback_llm = source.params.get("feedback_llm", True)
        hide_seen = source.params.get("hide_seen", False)
        confirm = source.params.get("confirm", False)
    fb = feedback_signals(user_id, with_digest=feedback_llm)
    prefs.expectations.feedback = fb.digest
    prefs.ranking.hidden_job_ids = fb.hidden_job_ids
    prefs.ranking.liked_job_ids = fb.liked_job_ids
    prefs.ranking.disliked_job_ids = fb.disliked_job_ids
    prefs.ranking.seen_job_ids = seen_ids(user_id) if hide_seen else []
    base_name = name.split(" · réajustée")[0]
    return create_search(
        user_id,
        cv_id,
        f"{base_name} · réajustée",
        prefs,
        skip_scrape=True,
        feedback_llm=feedback_llm,
        hide_seen=hide_seen,
        confirm=confirm,
    )


def _next_step(node: str, skip_scrape: bool) -> str | None:
    if node == "analyze_cv":
        return "rank_jobs" if skip_scrape else "scrape_jobs"
    if node == "scrape_jobs":
        return "rank_jobs"
    return None


def _update(search_id: int, **values) -> None:
    """Met à jour une recherche en cours (sans effet si un administrateur l'a arrêtée entre-temps)."""
    with session_scope() as session:
        session.execute(
            update(SearchSession)
            .where(SearchSession.id == search_id, SearchSession.status == "running")
            .values(**values)
        )


def run_search(search_id: int) -> None:
    """Exécute une session dans un thread de fond : analyse du CV, collecte, puis tri et évaluation.

    Si la recherche demande confirmation (`params["confirm"]`) et que des offres sont à évaluer par le
    LLM, elle s'arrête après la collecte à l'état « awaiting », avec l'aperçu de l'évaluation
    (`params["preview"]`) ; une fois confirmée (`confirm_search`), elle reprend directement au tri.
    """
    from getiajob.agents.ranker import preview_assessment, ranker_node
    from getiajob.graph import build_graph

    with session_scope() as session:
        search = session.get(SearchSession, search_id)
        if search is None or search.status != "pending":
            return
        resume = bool(search.params.get("confirmed"))
        search.status, search.step = "running", "rank_jobs" if resume else "analyze_cv"
        search.started_at = search.started_at if resume and search.started_at else datetime.now(UTC)
        params, cv_id, owner = dict(search.params), search.cv_id, search.user_id

    try:
        if cv_id is None:
            raise ValueError("Le CV utilisé par cette recherche a été supprimé.")
        skip = bool(params.get("skip_scrape"))
        state = {
            "cv_id": cv_id,
            "preferences": params["preferences"],
            "skip_scrape": skip,
            "force_rank": bool(params.get("force_rank")),
        }
        with search_context(search_id), usage.for_user(owner):
            if resume:  # collecte faite avant la confirmation
                if params.get("job_ids") is not None:
                    state["job_ids"] = params["job_ids"]
            else:
                for chunk in build_graph(rank=False).stream(state, stream_mode="updates"):
                    for node, node_update in chunk.items():
                        if node_update and "job_ids" in node_update:
                            state["job_ids"] = node_update["job_ids"]
                        _update(search_id, step=_next_step(node, skip))
                    check_cancelled(min_interval=0)
                if params.get("confirm") and _pause_for_confirmation(
                    search_id, params, state, preview_assessment
                ):
                    return
            _update(search_id, step="rank_jobs")
            results = ranker_node(state)["results"]
            check_cancelled(min_interval=0)

        job_ids = state.get("job_ids")
        _update(
            search_id,
            status="done",
            step=None,
            results=results,
            n_offers=len(job_ids) if job_ids is not None else None,
            finished_at=datetime.now(UTC),
        )
    except SearchCancelled:
        log.info("Recherche %s arrêtée : traitement interrompu.", search_id)
    except Exception as exc:
        log.exception("Échec de la recherche %s", search_id)
        _update(search_id, status="failed", step=None, error=str(exc)[:2000], finished_at=datetime.now(UTC))


def _pause_for_confirmation(search_id: int, params: dict, state: dict, preview_assessment) -> bool:
    """Met la recherche en attente de confirmation si le LLM a des offres à évaluer (True si en pause)."""
    prefs = Preferences.model_validate(params["preferences"])
    job_ids = state.get("job_ids")
    preview = preview_assessment(state["cv_id"], prefs, job_ids, force=state["force_rank"])
    if not any(c["eval"] for c in preview["candidates"][: prefs.ranking.prefilter_top_k]):
        return False  # tout est déjà en cache : rien à facturer, on poursuit sans demander
    _update(
        search_id,
        status=AWAITING,
        step=None,
        n_offers=len(job_ids) if job_ids is not None else None,
        params=params | {"job_ids": job_ids, "preview": preview},
    )
    return True


def confirm_search(user_id: int, search_id: int, max_offers: int) -> bool:
    """Confirme l'évaluation par le LLM des `max_offers` offres les plus proches du CV, et remet la
    recherche en file (le lancement est fait par l'appelant). False si elle n'attend plus de confirmation."""
    with session_scope() as session:
        search = session.scalar(
            select(SearchSession).where(
                SearchSession.id == search_id,
                SearchSession.user_id == user_id,
                SearchSession.status == AWAITING,
            )
        )
        if search is None:
            return False
        params = dict(search.params)
        prefs = dict(params["preferences"])
        prefs["ranking"] = dict(prefs["ranking"]) | {"prefilter_top_k": max(1, int(max_offers))}
        params |= {"preferences": prefs, "confirmed": True}
        params.pop("preview", None)
        search.params, search.status = params, "pending"
        return True


def list_searches(user_id: int, limit: int = 50) -> list[SearchSession]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(SearchSession)
                .where(SearchSession.user_id == user_id)
                .order_by(SearchSession.created_at.desc())
                .limit(limit)
            ).all()
        )


def get_search(user_id: int, search_id: int) -> SearchSession | None:
    with session_scope() as session:
        return session.scalar(
            select(SearchSession).where(SearchSession.id == search_id, SearchSession.user_id == user_id)
        )


def stop_search(user_id: int, search_id: int) -> bool:
    """Arrête une recherche en attente ou en cours de l'utilisateur.

    Une recherche en attente ne sera pas lancée ; une recherche en cours s'interrompt à la prochaine
    vérification (offre collectée ou évaluée), ce qui libère la file pour les recherches suivantes.
    """
    with session_scope() as session:
        result = session.execute(
            update(SearchSession)
            .where(
                SearchSession.id == search_id,
                SearchSession.user_id == user_id,
                SearchSession.status.in_(("pending", "running", AWAITING)),
            )
            .values(status="cancelled", step=None, error=STOPPED, finished_at=datetime.now(UTC))
        )
        return bool(result.rowcount)


def relaunch_search(user_id: int, search_id: int) -> bool:
    """Remet en file une recherche arrêtée (mêmes paramètres, collecte comprise). Le lancement est fait
    par l'appelant."""
    with session_scope() as session:
        search = session.scalar(
            select(SearchSession).where(
                SearchSession.id == search_id,
                SearchSession.user_id == user_id,
                SearchSession.status == "cancelled",
            )
        )
        if search is None:
            return False
        # Une confirmation donnée avant l'arrêt ne vaut pas pour la nouvelle collecte
        dropped = ("confirmed", "job_ids", "preview")
        search.params = {k: v for k, v in search.params.items() if k not in dropped}
        search.status, search.step, search.error = "pending", None, None
        search.results = search.n_offers = search.started_at = search.finished_at = None
        return True


def delete_search(user_id: int, search_id: int) -> None:
    with session_scope() as session:
        search = session.scalar(
            select(SearchSession).where(SearchSession.id == search_id, SearchSession.user_id == user_id)
        )
        if search and search.status not in ("pending", "running"):
            session.delete(search)


def queue_position(search_id: int) -> int:
    """Nombre de recherches à traiter avant celle-ci (toutes utilisateurs confondus)."""
    with session_scope() as session:
        return (
            session.scalar(
                select(func.count(SearchSession.id)).where(
                    SearchSession.status.in_(("pending", "running")), SearchSession.id < search_id
                )
            )
            or 0
        )
