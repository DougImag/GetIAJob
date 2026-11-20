"""Agent 2 — Collecte : planifie les requêtes à partir du profil puis interroge chaque source d'offres."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from getiajob.config import Preferences, get_settings, preferences_from_state
from getiajob.db import CVRecord, JobMatch, JobOffer, session_scope
from getiajob.llm import structured
from getiajob.schemas import CVProfile, SearchPlan
from getiajob.scraping.adzuna import AdzunaSource
from getiajob.scraping.base import (
    ALL_SOURCES,
    SOURCE_LABELS,
    JobPosting,
    JobSource,
    configured_sources,
    fingerprint,
)
from getiajob.scraping.francetravail import FranceTravailSource
from getiajob.scraping.linkedin import LinkedInSource
from getiajob.seniority import level_prompt, max_years
from getiajob.services.monitoring import record, timed

log = logging.getLogger(__name__)

DUPLICATE_WINDOW_DAYS = 45

PLANNER_PROMPT = """Tu es chasseur de têtes. À partir du profil d'un candidat et de ses attentes,
propose des requêtes de recherche d'emploi courtes (2 à 4 mots, pas d'opérateurs booléens),
utilisables sur LinkedIn, France Travail et Adzuna, dans la langue du marché visé,
de la plus pertinente à la moins pertinente.
Varie les intitulés (synonymes, variantes anglaises courantes) sans t'éloigner du profil.
Si un niveau débutant ou junior est visé, n'utilise aucun intitulé senior (senior, lead, expert,
confirmé…) et ajoute au moins une requête précisant le niveau (ex. « ingénieur IA junior »)."""


def plan_queries(profile: CVProfile, prefs: Preferences) -> list[str]:
    if prefs.search.keywords:
        return prefs.search.keywords

    levels = prefs.search.experience_levels
    level = f"Niveau visé : {level_prompt(levels)}\n" if levels and max_years(levels) is not None else ""
    llm = structured(SearchPlan)
    with timed("llm", "plan_requetes"):
        plan: SearchPlan = llm.invoke(
            [
                ("system", PLANNER_PROMPT),
                (
                    "human",
                    f"Profil :\n{profile.to_text()}\n\n"
                    f"Attentes :\n{level}{prefs.expectations.to_prompt()}\n\n"
                    f"Donne au plus {prefs.search.max_queries} requêtes.",
                ),
            ]
        )
    # Le poste indiqué dans la fiche utilisateur passe toujours en premier.
    desired = prefs.expectations.desired_job.strip()
    queries: list[str] = []
    for q in ([desired] if desired else []) + (plan.queries or profile.target_job_titles):
        q = q.strip()
        if q and q.lower() not in {x.lower() for x in queries}:
            queries.append(q)
    return queries[: prefs.search.max_queries]


def available_sources(wanted: list[str] | None = None) -> list[JobSource]:
    """Sources demandées et utilisables (France Travail / Adzuna : seulement si leurs clés existent)."""
    s = get_settings()
    wanted = wanted if wanted is not None else ALL_SOURCES
    sources: list[JobSource] = []
    for name in wanted:
        if name == "linkedin":
            sources.append(LinkedInSource(delay=s.linkedin_request_delay))
        elif name == "francetravail":
            if s.francetravail_client_id and s.francetravail_client_secret:
                sources.append(FranceTravailSource(s.francetravail_client_id, s.francetravail_client_secret))
            else:
                log.info("France Travail ignoré : FRANCETRAVAIL_CLIENT_ID / _SECRET non renseignés.")
        elif name == "adzuna":
            if s.adzuna_app_id and s.adzuna_app_key:
                sources.append(AdzunaSource(s.adzuna_app_id, s.adzuna_app_key, s.adzuna_request_delay))
            else:
                log.info("Adzuna ignoré : ADZUNA_APP_ID / ADZUNA_APP_KEY non renseignés.")
    return sources


def _find_duplicate(
    session: Session, posting: JobPosting, fp: str | None, owner_id: int | None = None
) -> JobOffer | None:
    """Même offre déjà collectée via une autre source (même titre + entreprise, vue récemment).

    Une offre publique ne fusionne qu'avec une offre publique ; une offre privée (alerte e-mail)
    avec une offre publique ou une offre du même utilisateur.
    """
    if fp is None:
        return None
    since = datetime.now(UTC) - timedelta(days=DUPLICATE_WINDOW_DAYS)
    visible = (
        JobOffer.owner_user_id.is_(None)
        if owner_id is None
        else or_(JobOffer.owner_user_id.is_(None), JobOffer.owner_user_id == owner_id)
    )
    return session.scalar(
        select(JobOffer)
        .where(
            JobOffer.fingerprint == fp,
            JobOffer.source != posting.source,
            JobOffer.last_seen_at >= since,
            visible,
        )
        .order_by(JobOffer.last_seen_at.desc())
        .limit(1)
    )


def _content_changed(session: Session, job: JobOffer) -> None:
    """Le texte de l'offre a changé : son vecteur et ses notes IA doivent être recalculés.

    On efface seulement la clé d'évaluation : l'ancienne note reste affichée d'ici là, et le
    classeur la réévalue à la prochaine recherche qui retient cette offre.
    """
    job.embedding = None
    if job.id is not None:
        session.execute(update(JobMatch).where(JobMatch.job_id == job.id).values(assessment_key=None))


def store_posting(
    session: Session, source: JobSource | None, posting: JobPosting, query: str, owner_id: int | None = None
) -> JobOffer:
    """Insère ou met à jour l'offre ; fusionne les doublons entre sources.

    `owner_id` : offre privée de cet utilisateur (alertes e-mail), sinon publique.
    """
    fp = fingerprint(posting.title, posting.company)
    job = session.scalar(select(JobOffer).where(JobOffer.external_id == posting.external_id))
    if job is None and (job := _find_duplicate(session, posting, fp, owner_id)) is not None:
        # Déjà connue ailleurs : on garde l'offre existante, en complétant ce qui manque.
        record("dedup", posting.source)
        if posting.description and len(posting.description) > len(job.description or ""):
            job.description = posting.description
            _content_changed(session, job)
        job.salary = job.salary or posting.salary
        job.criteria = {**posting.criteria, **(job.criteria or {})}
        job.last_seen_at = datetime.now(UTC)
        return job
    if job is None:
        job = JobOffer(
            external_id=posting.external_id,
            source=posting.source,
            fingerprint=fp,
            owner_user_id=owner_id,
            title=posting.title[:500],
            company=posting.company,
            location=posting.location,
            url=posting.url[:1000],
            posted_at=posting.posted_at,
            search_query=query[:255],
            criteria={},
        )
        session.add(job)
    job.last_seen_at = datetime.now(UTC)

    if not job.description:
        if not posting.description and source is not None:
            try:
                posting = source.details(posting)
            except Exception as exc:  # une offre en échec ne bloque pas la collecte
                log.warning("Détail indisponible pour %s : %s", posting.external_id, exc)
        if posting.description:
            job.description = posting.description
            job.salary = posting.salary
            job.criteria = posting.criteria
            _content_changed(session, job)
    return job


def _alert_offers(user_id: int | None, days: int) -> set[int]:
    """Relève la boîte des alertes e-mail puis renvoie les offres reçues par l'utilisateur."""
    from getiajob.services.mail_alerts import sync_mailbox, user_alert_job_ids

    if user_id is None:
        return set()
    report = sync_mailbox()
    if report.error:
        log.warning("Alertes e-mail : %s", report.error)
    ids = set(user_alert_job_ids(user_id, days))
    log.info("Alertes e-mail : %d offre(s) sur la période.", len(ids))
    return ids


def scrape_jobs(profile: CVProfile, prefs: Preferences, user_id: int | None = None) -> list[int]:
    wanted = prefs.search.sources
    job_ids: set[int] = set()
    if "apec" in wanted and "apec" in configured_sources():
        job_ids |= _alert_offers(user_id, prefs.search.days)

    queries = plan_queries(profile, prefs)
    sources = available_sources([s for s in wanted if s != "apec"])
    log.info("Requêtes : %s — sources : %s", queries, [s.name for s in sources])

    with session_scope() as session:
        for source in sources:
            label = SOURCE_LABELS.get(source.name, source.name)
            try:
                for query in queries:
                    for location in prefs.search.locations:
                        found: set[int] = set()
                        started = time.monotonic()
                        for posting in source.search(
                            query,
                            location,
                            days=prefs.search.days,
                            limit=prefs.search.max_offers_per_query,
                            remote=prefs.search.remote,
                            experience_levels=prefs.search.experience_levels,
                        ):
                            job = store_posting(session, source, posting, query)
                            session.flush()
                            found.add(job.id)
                        session.commit()  # on sauvegarde la progression après chaque requête
                        job_ids |= found
                        record(
                            "source",
                            source.name,
                            count=len(found),
                            duration_ms=int((time.monotonic() - started) * 1000),
                        )
                        log.info("%s — « %s » @ %s : %d offres", label, query, location, len(found))
            except Exception as exc:  # une source en panne n'empêche pas les autres
                session.rollback()
                log.warning("%s indisponible, source ignorée : %s", label, exc)
                record("source", source.name, ok=False, count=0, detail=f"{type(exc).__name__} : {exc}")
            finally:
                source.close()

    log.info("%d offres uniques collectées.", len(job_ids))
    return sorted(job_ids)


def job_scraper_node(state: dict) -> dict:
    profile = CVProfile.model_validate(state["profile"])
    user_id = None
    if state.get("cv_id") is not None:
        with session_scope() as session:
            cv = session.get(CVRecord, state["cv_id"])
            user_id = cv.user_id if cv else None
    return {"job_ids": scrape_jobs(profile, preferences_from_state(state), user_id)}
