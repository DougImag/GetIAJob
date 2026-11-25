from getiajob.agents.ranker import hard_filter_reason
from getiajob.config import Expectations
from getiajob.db import JobOffer
from getiajob.seniority import (
    JUNIOR_BONUS,
    MID_PENALTY,
    NEAR_PENALTY,
    level_check,
    max_years,
    required_years,
)


def test_required_years_patterns():
    assert required_years("Minimum de 3 ans d'expérience dans un rôle similaire") == 3
    assert required_years("5+ ans d'expérience dans des rôles MLOps") == 5
    assert required_years("6/8 ans d'experience minimum") == 6
    assert required_years("Profil de 3 à 7 ans d'expérience pertinente") == 3
    assert required_years("6+ years as a hands-on engineer") == 6
    assert required_years("Vous avez au moins 3 ans de développement (back, data)") == 3
    assert required_years("4 ans en CDI et plus en développement logiciel") == 4
    assert required_years("2 à 5 ans d'expérience en ML engineering") == 2


def test_required_years_ignores_company_age():
    assert required_years("Depuis plus de 30 ans, nous construisons les fondations data") is None
    assert required_years("Fort de plus de 30 ans d'expertise et de 2 800 collaborateurs") is None
    assert required_years("Your data is kept for up to 2 years in our candidate pool") is None
    assert required_years(None) is None


def test_max_years():
    assert max_years([]) is None
    assert max_years(["entry"]) == 2
    assert max_years(["entry", "associate"]) == 4
    assert max_years(["entry", "mid-senior"]) is None


def test_level_check_for_beginner():
    entry = ["entry"]
    assert "senior" in level_check("Senior AI Engineer", "", {}, entry)[0]
    assert "senior" in level_check("Lead IA Engineer", "", {}, entry)[0]
    assert "senior" in level_check("Ingénieur IA CONFIRME - Paris", "", {}, entry)[0]
    assert "senior" in level_check("Ingénieur(e) IA Expérimenté(e)", "", {}, entry)[0]
    assert "5 ans" in level_check("AI Engineer", "5+ ans d'expérience requis", {}, entry)[0]
    assert level_check("AI Engineer", "3 ans d'expérience minimum", {}, entry) == (None, NEAR_PENALTY)
    # Affichée « Premier emploi » par la source : gardée malgré les années demandées
    first_job = {"Niveau hiérarchique": "Premier emploi"}
    assert level_check("AI Python Developer", "au moins 3 ans d'expérience", first_job, entry) == (None, 0.0)
    assert level_check("Stage – Data Scientist", "", {}, entry)[0] == "stage / alternance"
    assert level_check("Stage – Data Scientist", "", {}, ["internship"])[0] is None
    # Offres compatibles
    assert level_check("AI Engineer", "2 à 5 ans d'expérience", {}, entry) == (None, 0.0)
    assert level_check("Ingénieur IA - Débutant", "", {}, entry) == (None, JUNIOR_BONUS)
    assert level_check("Junior / Senior Data Scientist", "", {}, entry) == (None, JUNIOR_BONUS)
    assert level_check("ML Engineer", "", {"Niveau hiérarchique": "Premier emploi"}, entry) == (
        None,
        JUNIOR_BONUS,
    )
    assert level_check("ML Engineer", "", {"Niveau hiérarchique": "Confirmé"}, entry) == (None, MID_PENALTY)
    mid = {"Niveau hiérarchique": "Confirmé"}
    assert level_check("ML Engineer", "1 à 2 ans d'expérience", mid, entry) == (None, 0.0)


def test_level_check_without_constraint():
    assert level_check("Senior AI Engineer", "8 ans d'expérience", {}, []) == (None, 0.0)
    assert level_check("Senior AI Engineer", "", {}, ["mid-senior"]) == (None, 0.0)


def test_france_travail_experience_criteria():
    crit = {"Expérience": "Expérience exigée de 5 An(s)"}
    assert "5 ans" in level_check("Développeur Python", "", crit, ["entry"])[0]
    assert (
        level_check("Développeur Python", "", {"Expérience": "Débutant accepté"}, ["entry"])[1]
        == JUNIOR_BONUS
    )


def test_hard_filter_with_levels():
    job = JobOffer(title="Senior AI Engineer", company="ACME", description="Python")
    assert hard_filter_reason(job, Expectations()) is None
    assert "senior" in hard_filter_reason(job, Expectations(), ["entry"])
