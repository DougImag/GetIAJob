from datetime import UTC, datetime

from getiajob.agents.ranker import combine_scores
from getiajob.config import Preferences
from getiajob.services.feedback import (
    Feedback,
    build_digest,
    closeness,
    feedback_adjustment,
    signals_from,
)
from getiajob.services.searches import SearchOptions, build_preferences
from getiajob.services.users import ProfileData

NOW = datetime(2026, 11, 13, tzinfo=UTC)


def fb(job_id, rating, reasons=(), comment=None, title="Data Analyst", company="ACME"):
    return Feedback(job_id, rating, list(reasons), comment, title, company, NOW)


FEEDBACK = [
    fb(1, 1, comment="MLOps en production", title="ML Engineer", company="Mistral"),
    fb(2, -1, ["too_senior", "tech"], "trop orienté BI"),
    fb(3, -1, ["company"], company="BigCorp"),
    fb(4, -1, ["seen"]),
    fb(5, -1),  # sans motif : considéré comme un refus sur le fond
]


def test_signals_split_hidden_liked_and_penalized():
    sig = signals_from(FEEDBACK)
    assert sig.liked_job_ids == [1]
    assert sig.hidden_job_ids == [2, 3, 4, 5]
    # « entreprise » et « déjà vue » ne pénalisent pas les offres similaires
    assert sig.disliked_job_ids == [2, 5]
    assert signals_from(FEEDBACK, with_digest=False).digest == ""


def test_digest_is_compact_and_readable():
    digest = build_digest(FEEDBACK)
    assert "Motifs de refus fréquents" in digest
    assert "niveau trop senior (1)" in digest
    assert "ML Engineer (Mistral) — « MLOps en production »" in digest
    assert "« trop orienté BI »" in digest
    assert "Déjà vue" not in digest  # les offres simplement déjà vues n'apprennent rien
    assert len(build_digest(FEEDBACK * 50, max_chars=500)) <= 500
    assert build_digest([]) == ""


def test_feedback_adjustment():
    assert closeness(0.70) == 0
    assert closeness(0.99) == 1
    assert feedback_adjustment(None, None, 10) == 0
    assert feedback_adjustment(0.95, None, 10) == 10
    assert feedback_adjustment(None, 0.95, 10) == -10
    assert feedback_adjustment(0.72, 0.72, 10) == 0  # offres sans rapport : pas d'effet
    assert combine_scores(0.8, 90, 0.7, adjustment=-10) == 77.0
    assert combine_scores(1.0, 100, 0.7, adjustment=10) == 100.0


def test_preferences_carry_feedback():
    prefs = build_preferences(
        ProfileData(desired_job="Data Engineer"), SearchOptions(), Preferences(), signals_from(FEEDBACK)
    )
    assert prefs.ranking.hidden_job_ids == [2, 3, 4, 5]
    assert prefs.ranking.liked_job_ids == [1]
    assert "Retours du candidat" in prefs.expectations.to_prompt()
    assert Preferences.model_validate(prefs.model_dump(mode="json")) == prefs
    assert (
        "Retours du candidat"
        not in build_preferences(ProfileData(), SearchOptions(), Preferences()).expectations.to_prompt()
    )
