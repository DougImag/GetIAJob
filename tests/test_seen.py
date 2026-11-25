from datetime import UTC, datetime

from getiajob.web.views.results import _card_header

OFFER = {"final_score": 72.0, "title": "Ingénieur IA", "url": "https://example.com/1", "summary": "ok"}


def test_card_header_marks_seen_offer():
    html = _card_header(1, OFFER, datetime(2026, 11, 24, 10, 0, tzinfo=UTC))
    assert 'class="gj-seen"' in html
    assert "Consultée le 24/11" in html


def test_card_header_unseen_offer_has_no_badge():
    assert "gj-seen" not in _card_header(1, OFFER)


def test_preferences_carry_seen_offers():
    from getiajob.config import Preferences
    from getiajob.services.searches import SearchOptions, build_preferences
    from getiajob.services.users import ProfileData

    prefs = build_preferences(ProfileData(), SearchOptions(), Preferences(), seen_job_ids=[4, 7])
    assert prefs.ranking.seen_job_ids == [4, 7]
    assert Preferences.model_validate(prefs.model_dump(mode="json")) == prefs
    assert build_preferences(ProfileData(), SearchOptions(), Preferences()).ranking.seen_job_ids == []


def test_search_chips_show_excluded_seen_offers():
    from types import SimpleNamespace

    from getiajob.web.views.results import _criteria_chips

    s = SimpleNamespace(params={"preferences": {"ranking": {"seen_job_ids": [1, 2, 3]}}})
    assert "3 offre(s) déjà consultée(s) écartée(s)" in _criteria_chips(s)
