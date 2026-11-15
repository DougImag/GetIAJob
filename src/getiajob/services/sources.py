"""Sources d'annonces : statistiques en base et recherche de test (Admin › Sources d'annonces)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import islice

from sqlalchemy import func, select

from getiajob.agents.job_scraper import available_sources
from getiajob.db import JobOffer, session_scope
from getiajob.scraping.base import ALL_SOURCES, JobPosting

MAX_TEST_RESULTS = 30
LINKEDIN_TEST_DETAILS = 3  # détail récupéré pour quelques offres seulement (une requête par offre)


@dataclass(frozen=True)
class SourceStats:
    total: int = 0
    last_7_days: int = 0
    last_seen_at: datetime | None = None


@dataclass
class SourceTest:
    source: str
    keywords: str
    location: str
    days: int
    postings: list[JobPosting] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def ok(self) -> bool:
        return self.error is None


def source_stats() -> dict[str, SourceStats]:
    since = datetime.now(UTC) - timedelta(days=7)
    with session_scope() as session:
        rows = session.execute(
            select(
                JobOffer.source,
                func.count(JobOffer.id),
                func.count(JobOffer.id).filter(JobOffer.last_seen_at >= since),
                func.max(JobOffer.last_seen_at),
            ).group_by(JobOffer.source)
        ).all()
    stats = {name: SourceStats() for name in ALL_SOURCES}
    for name, total, recent, last in rows:
        stats[name] = SourceStats(total, recent, last)
    return stats


def try_source(name: str, keywords: str, location: str, days: int = 7, limit: int = 10) -> SourceTest:
    """Interroge une source sans rien enregistrer en base (diagnostic)."""
    result = SourceTest(name, keywords, location, days)
    sources = available_sources([name])
    if not sources:
        result.error = "Source non configurée : renseigner ses clés d'API dans le fichier .env."
        return result
    source = sources[0]
    start = time.monotonic()
    try:
        limit = max(1, min(MAX_TEST_RESULTS, limit))
        result.postings = list(islice(source.search(keywords, location, days=days, limit=limit), limit))
        for posting in result.postings[:LINKEDIN_TEST_DETAILS]:
            if not posting.description:
                source.details(posting)
    except Exception as exc:  # on affiche l'erreur telle quelle : c'est le but du test
        result.error = f"{type(exc).__name__} : {exc}"
    finally:
        source.close()
        result.seconds = time.monotonic() - start
    return result
