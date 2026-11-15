from datetime import UTC, datetime
from pathlib import Path

from getiajob.scraping.linkedin import parse_job_details, parse_search_results

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_search_results():
    cards = parse_search_results((FIXTURES / "search_results.html").read_text())
    assert [c.linkedin_id for c in cards] == ["4012345678", "4087654321"]

    first = cards[0]
    assert first.title == "Data Engineer"
    assert first.company == "ACME"
    assert first.location == "Paris, Île-de-France, France"
    assert first.url == "https://fr.linkedin.com/jobs/view/data-engineer-at-acme-4012345678"
    assert first.posted_at == datetime(2026, 11, 10, tzinfo=UTC)

    second = cards[1]
    assert second.title == "ML Engineer"
    assert second.url == "https://fr.linkedin.com/jobs/view/ml-engineer-4087654321"
    assert second.posted_at is None


def test_parse_search_results_empty():
    assert parse_search_results("") == []


def test_parse_job_details():
    details = parse_job_details((FIXTURES / "job_details.html").read_text())
    assert "Data Engineer" in details.description
    assert "Airflow" in details.description
    assert details.salary == "55 000 € - 65 000 €/an"
    assert details.criteria == {
        "Niveau hiérarchique": "Confirmé",
        "Type d’emploi": "Temps plein",
    }
