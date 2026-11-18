from contextlib import contextmanager
from types import SimpleNamespace

from getiajob.services import searches, users
from getiajob.services.searches import SearchOptions
from getiajob.services.users import ProfileData
from getiajob.web.views.search import _clamp


def fake_db(monkeypatch, module, row):
    session = SimpleNamespace(get=lambda model, user_id: row, add=lambda obj: None)

    @contextmanager
    def scope():
        yield session

    monkeypatch.setattr(module, "session_scope", scope)


def test_default_top_n_is_20():
    assert SearchOptions().top_n == 20


def test_search_form_round_trip(monkeypatch):
    row = SimpleNamespace(search_options=None)
    fake_db(monkeypatch, searches, row)
    opts = SearchOptions(top_n=35, locations=["Nantes"], max_queries=6)
    searches.save_search_form(3, opts, cv_id=6, use_feedback=False, force_rank=True)
    saved = searches.load_search_form(3)
    assert saved["top_n"] == 35 and saved["locations"] == ["Nantes"] and saved["max_queries"] == 6
    assert saved["cv_id"] == 6 and saved["use_feedback"] is False
    assert "force_rank" not in saved

    searches.reset_search_form(3)
    assert searches.load_search_form(3) == {}


def test_saving_profile_forgets_profile_fields(monkeypatch):
    row = SimpleNamespace(search_options={"top_n": 35, "locations": ["Nantes"], "remote": ["full"]})
    fake_db(monkeypatch, users, row)
    users.save_profile(3, ProfileData(locations=["Rennes"]))
    assert row.search_options == {"top_n": 35}
    assert row.locations == ["Rennes"]


def test_clamp():
    assert _clamp(80, 3, 50, 20) == 50
    assert _clamp(None, 3, 50, 20) == 20
    assert _clamp(0.4, 0.0, 1.0, 0.7) == 0.4
