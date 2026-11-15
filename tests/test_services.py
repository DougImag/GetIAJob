import pytest

from getiajob.agents.cv_analyzer import extract_text
from getiajob.config import Preferences, SearchPreferences
from getiajob.services.searches import SearchOptions, _next_step, build_preferences, clamp_days
from getiajob.services.users import (
    AuthError,
    ProfileData,
    hash_password,
    validate_password,
    verify_password,
)
from getiajob.web.layout import badges, parse_list


def test_password_hashing():
    hashed = hash_password("motdepasse-solide")
    assert verify_password("motdepasse-solide", hashed)
    assert not verify_password("mauvais", hashed)


@pytest.mark.parametrize("pw", ["court", "é" * 40])
def test_password_validation_rejects(pw):
    with pytest.raises(AuthError):
        validate_password(pw)


def test_profile_completion():
    assert ProfileData().completion()[0] == 0
    full = ProfileData(
        desired_job="Data Engineer",
        locations=["Paris"],
        remote=["hybrid"],
        contract_types=["CDI"],
    )
    assert full.completion() == (100, [])


def test_freshness_is_clamped():
    assert clamp_days(7) == 7
    assert clamp_days(0) == 1
    assert clamp_days(90) == 30
    assert ProfileData().max_offer_age_days == 7


def test_build_preferences_merges_profile_and_options():
    profile = ProfileData(
        desired_job="Data Engineer",
        locations=["Lyon"],
        max_offer_age_days=3,
        contract_types=["CDI"],
        min_salary=55000,
        expectations="Équipe produit",
    )
    base = Preferences(search=SearchPreferences(locations=["France"], max_queries=2))
    opts = SearchOptions(
        max_age_days=14,
        top_n=40,
        prefilter_top_k=20,
        exclude_keywords=["stage"],
    )
    prefs = build_preferences(profile, opts, base=base)

    assert prefs.search.locations == ["Lyon"]  # la fiche prime sur la config
    assert prefs.expectations.desired_job == "Data Engineer"
    assert prefs.expectations.min_salary == 55000
    assert prefs.expectations.exclude_keywords == ["stage"]
    assert prefs.ranking.top_n == 40
    assert prefs.ranking.prefilter_top_k == 40  # jamais moins que le nombre de résultats voulu
    assert "Poste recherché : Data Engineer" in prefs.expectations.to_prompt()
    assert prefs.search.days == 14  # l'option de la recherche prime sur la fiche
    # sérialisable pour être stocké en JSONB puis relu par le pipeline
    assert Preferences.model_validate(prefs.model_dump(mode="json")) == prefs


def test_option_locations_override_profile():
    prefs = build_preferences(
        ProfileData(locations=["Lyon"]),
        SearchOptions(locations=["Remote"]),
        base=Preferences(),
    )
    assert prefs.search.locations == ["Remote"]


def test_next_step():
    assert _next_step("analyze_cv", skip_scrape=False) == "scrape_jobs"
    assert _next_step("analyze_cv", skip_scrape=True) == "rank_jobs"
    assert _next_step("scrape_jobs", skip_scrape=False) == "rank_jobs"
    assert _next_step("rank_jobs", skip_scrape=False) is None


def test_extract_text_plain():
    assert extract_text("Développeuse Python".encode(), "cv.md") == "Développeuse Python"


def test_parse_list_and_badges():
    assert parse_list("Paris, lyon ,PARIS,\nLille") == ["Paris", "lyon", "Lille"]
    assert badges(["C++", "a[b]"], "green") == ":green-badge[C++] :green-badge[a(b)]"
