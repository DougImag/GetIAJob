"""Une offre dont le texte change doit être réévaluée par l'IA (sans base : session simulée)."""

from __future__ import annotations

from getiajob.agents import job_scraper
from getiajob.db import JobOffer
from getiajob.scraping.base import JobPosting


class FakeSession:
    def __init__(self, existing: JobOffer | None):
        self.existing = existing
        self.statements: list = []

    def scalar(self, stmt):
        return self.existing

    def execute(self, stmt):
        self.statements.append(stmt)

    def add(self, obj):
        pass


def _posting(description: str | None) -> JobPosting:
    return JobPosting(
        source="adzuna", source_id="1", title="Chef de projet", url="u", description=description
    )


def _invalidations(session: FakeSession) -> list[str]:
    return [str(s) for s in session.statements if "job_matches" in str(s) and "assessment_key" in str(s)]


def test_new_description_invalidates_existing_scores():
    job = JobOffer(id=42, external_id="adz:1", title="Chef de projet", url="u", embedding=[0.1])
    session = FakeSession(job)
    job_scraper.store_posting(session, None, _posting("Description complète"), "q")
    assert job.description == "Description complète" and job.embedding is None
    assert len(_invalidations(session)) == 1


def test_unchanged_offer_keeps_scores():
    job = JobOffer(id=42, external_id="adz:1", title="Chef de projet", url="u", description="Déjà là")
    session = FakeSession(job)
    job_scraper.store_posting(session, None, _posting("Autre texte"), "q")
    assert job.description == "Déjà là" and not _invalidations(session)


def test_new_offer_needs_no_invalidation():
    session = FakeSession(None)
    job = job_scraper.store_posting(session, None, _posting("Texte"), "q")
    assert job.description == "Texte" and not session.statements
