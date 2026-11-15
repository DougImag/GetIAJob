"""Sources d'offres France Travail et Adzuna (réponses d'API simulées, aucun appel réseau)."""

from __future__ import annotations

import httpx

from getiajob.scraping import adzuna, francetravail
from getiajob.scraping.adzuna import AdzunaSource
from getiajob.scraping.base import JobPosting, clean_html, fingerprint
from getiajob.scraping.francetravail import FranceTravailSource, published_since

FT_OFFER = {
    "id": "185ABCD",
    "intitule": "Chef de projet digital H/F",
    "description": "Pilotage de la <b>Digital Factory</b>.<br>Méthodes agiles.",
    "dateCreation": "2026-11-10T09:30:00.000Z",
    "lieuTravail": {"libelle": "75 - Paris 9e Arrondissement", "commune": "75109"},
    "entreprise": {"nom": "ACME"},
    "typeContratLibelle": "Contrat à durée indéterminée",
    "experienceLibelle": "5 An(s)",
    "salaire": {"libelle": "Annuel de 55000,00 Euros à 65000,00 Euros"},
    "competences": [{"libelle": "Gestion de projet"}],
    "origineOffre": {"origine": "1"},
}

ADZ_OFFER = {
    "id": 4567890123,
    "title": "<strong>Chef de projet</strong> digital (H/F)",
    "description": "Rattaché au DSI, vous pilotez&hellip;",
    "created": "2026-11-11T08:00:00Z",
    "redirect_url": "https://www.adzuna.fr/land/ad/4567890123",
    "company": {"display_name": "ACME SAS"},
    "location": {"display_name": "Paris, Ile-de-France"},
    "salary_min": 50000,
    "salary_max": 60000,
    "salary_is_predicted": "1",
    "contract_type": "permanent",
    "contract_time": "full_time",
}


def _ft_source(handler) -> FranceTravailSource:
    source = FranceTravailSource(
        "id", "secret", delay=0, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    source._token, source._token_expiry = "tok", float("inf")
    return source


def test_francetravail_offer_parsing():
    job = francetravail.parse_offer(FT_OFFER)
    assert job.external_id == "ft:185ABCD"
    assert job.company == "ACME"
    assert job.url.endswith("/detail/185ABCD")
    assert "Digital Factory" in job.description and "<b>" not in job.description
    assert "Compétences : Gestion de projet" in job.description
    assert job.criteria["Contrat"] == "Contrat à durée indéterminée"
    assert job.posted_at.year == 2026


def test_francetravail_partner_offer_links_to_origin():
    item = {
        **FT_OFFER,
        "origineOffre": {
            "urlOrigine": "https://partenaire.example/o/1",
            "partenaires": [{"nom": "HELLOWORK"}],
        },
    }
    job = francetravail.parse_offer(item)
    assert job.url == "https://partenaire.example/o/1"
    assert job.criteria["Diffusée par"] == "HELLOWORK"


def test_francetravail_published_since_uses_allowed_values():
    assert [published_since(d) for d in (1, 2, 7, 10, 30)] == [1, 3, 7, 14, 31]


def test_francetravail_location_resolution():
    source = _ft_source(lambda r: httpx.Response(500))
    source._places = {"lyon": {"commune": "69123"}, "rhone": {"departement": "69"}}
    assert source.location_params("France") == {}
    assert source.location_params("Paris") == {"departement": "75"}
    assert source.location_params("92") == {"departement": "92"}
    assert source.location_params("78800") == {"departement": "78"}
    assert source.location_params("Lyon") == {"commune": "69123", "distance": "10"}
    assert source.location_params("Rhône") == {"departement": "69"}
    assert source.location_params("Atlantide") is None


def test_francetravail_search_paginates_and_stops():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        if request.url.params["range"] == "0-149":
            return httpx.Response(206, json={"resultats": [{**FT_OFFER, "id": str(i)} for i in range(150)]})
        return httpx.Response(206, json={"resultats": [{**FT_OFFER, "id": "last"}]})

    jobs = list(_ft_source(handler).search("chef de projet", "Paris", days=7, limit=200))
    assert len(jobs) == 151
    assert [c["range"] for c in calls] == ["0-149", "150-199"]
    assert calls[0]["departement"] == "75" and calls[0]["publieeDepuis"] == "7"


def test_francetravail_no_content():
    assert list(_ft_source(lambda r: httpx.Response(204)).search("x", "France")) == []


def test_adzuna_offer_parsing():
    job = adzuna.parse_offer(ADZ_OFFER)
    assert job.external_id == "adz:4567890123"
    assert job.title == "Chef de projet digital (H/F)"
    assert job.description.endswith("pilotez…")
    assert job.salary == "50 000 – 60 000 € / an (estimation Adzuna)"
    assert job.criteria == {"Contrat": "CDI", "Durée du travail": "Temps plein"}


def test_adzuna_search_params_and_limit():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": [{**ADZ_OFFER, "id": i} for i in range(50)]})

    source = AdzunaSource("app", "key", delay=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    jobs = list(source.search("chef de projet", "France", days=14, limit=60))
    assert len(jobs) == 60
    assert [r.url.path.rsplit("/", 1)[-1] for r in seen] == ["1", "2"]
    params = seen[0].url.params
    assert params["max_days_old"] == "14" and "where" not in params and params["app_id"] == "app"


def test_fingerprint_matches_same_offer_across_sites():
    ft = francetravail.parse_offer(FT_OFFER)
    adz = adzuna.parse_offer(ADZ_OFFER)
    assert fingerprint(ft.title, ft.company) == fingerprint(adz.title, adz.company)
    assert fingerprint("Chef de projet digital", "Autre SA") != fingerprint(ft.title, ft.company)
    assert fingerprint("Chef de projet digital", None) is None


def test_linkedin_ids_keep_no_prefix():
    assert JobPosting(source="linkedin", source_id="401", title="t", url="u").external_id == "401"


def test_clean_html():
    assert clean_html("<p>Un&nbsp;<b>test</b></p><p>Deux</p>") == "Un test\nDeux"
    assert clean_html("") is None


def test_try_source_reports_results_and_errors(monkeypatch):
    from getiajob.services import sources

    class Fake:
        name = "adzuna"
        closed = False

        def search(self, keywords, location, *, days, limit, **_):
            return iter([adzuna.parse_offer(ADZ_OFFER)] * 50)

        def details(self, posting):
            return posting

        def close(self):
            Fake.closed = True

    monkeypatch.setattr(sources, "available_sources", lambda names: [Fake()])
    res = sources.try_source("adzuna", "chef de projet", "Paris", 7, limit=5)
    assert res.ok and len(res.postings) == 5 and Fake.closed

    class Broken(Fake):
        def search(self, *a, **k):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(sources, "available_sources", lambda names: [Broken()])
    res = sources.try_source("adzuna", "x", "France")
    assert not res.ok and "ConnectError" in res.error

    monkeypatch.setattr(sources, "available_sources", lambda names: [])
    assert "non configurée" in sources.try_source("francetravail", "x", "France").error


def test_francetravail_rejected_credentials_give_clear_error():
    import pytest

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_client"})

    source = FranceTravailSource(
        "id", "bad", delay=0, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(francetravail.FranceTravailAuthError, match="invalid_client"):
        list(source.search("x", "France"))


def test_adzuna_ignores_hourly_salary_and_unknown_category():
    job = adzuna.parse_offer(
        {
            **ADZ_OFFER,
            "salary_min": 5,
            "salary_max": 5,
            "category": {"label": "Unknown"},
            "contract_time": None,
        }
    )
    assert job.salary is None
    assert job.criteria == {"Contrat": "CDI"}
