"""Sessions de recherche : construction des préférences, exécution en tâche de fond, historique."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from getiajob.config import Expectations, Preferences, RankingPreferences, load_preferences
from getiajob.db import SearchSession, UserProfile, session_scope
from getiajob.services.cvs import owns_cv
from getiajob.services.feedback import FeedbackSignals, feedback_signals
from getiajob.services.users import ProfileData

log = logging.getLogger(__name__)

STEP_LABELS = {
    "analyze_cv": "Analyse du CV",
    "scrape_jobs": "Collecte des offres",
    "rank_jobs": "Tri et évaluation des offres",
}
STATUS_LABELS = {
    "pending": "En attente",
    "running": "En cours",
    "done": "Terminée",
    "failed": "Échec",
}
MAX_DAYS = 30


@dataclass
class SearchOptions:
    """Paramètres saisis dans le formulaire « Nouvelle recherche »."""

    max_age_days: int = 7  # fraîcheur des annonces
    top_n: int = 20
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
FORM_EXTRAS = ("cv_id", "use_feedback", "feedback_llm")


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
) -> Preferences:
    """Fusionne config YAML (valeurs par défaut) < fiche utilisateur < options de la recherche.

    `feedback` : avis de l'utilisateur sur des offres précédentes, figés dans la recherche.
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
        llm_weight=opts.llm_weight,
        hidden_job_ids=feedback.hidden_job_ids,
        liked_job_ids=feedback.liked_job_ids,
        disliked_job_ids=feedback.disliked_job_ids,
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
) -> int:
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
    fb = feedback_signals(user_id, with_digest=feedback_llm)
    prefs.expectations.feedback = fb.digest
    prefs.ranking.hidden_job_ids = fb.hidden_job_ids
    prefs.ranking.liked_job_ids = fb.liked_job_ids
    prefs.ranking.disliked_job_ids = fb.disliked_job_ids
    base_name = name.split(" · réajustée")[0]
    return create_search(
        user_id, cv_id, f"{base_name} · réajustée", prefs, skip_scrape=True, feedback_llm=feedback_llm
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
    """Exécute le pipeline LangGraph pour une session (appelé dans un thread de fond)."""
    from getiajob.graph import build_graph

    with session_scope() as session:
        search = session.get(SearchSession, search_id)
        if search is None or search.status != "pending":
            return
        search.status, search.step, search.started_at = "running", "analyze_cv", datetime.now(UTC)
        params, cv_id = dict(search.params), search.cv_id

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
        final: dict = {}
        for chunk in build_graph().stream(state, stream_mode="updates"):
            for node, node_update in chunk.items():
                final.update(node_update or {})
                _update(search_id, step=_next_step(node, skip))

        _update(
            search_id,
            status="done",
            step=None,
            results=final.get("results", []),
            n_offers=len(final["job_ids"]) if "job_ids" in final else None,
            finished_at=datetime.now(UTC),
        )
    except Exception as exc:
        log.exception("Échec de la recherche %s", search_id)
        _update(search_id, status="failed", step=None, error=str(exc)[:2000], finished_at=datetime.now(UTC))


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
