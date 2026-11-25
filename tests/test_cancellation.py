from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from getiajob.services import cancellation
from getiajob.services.cancellation import SearchCancelled, check_cancelled, search_context


def fake_status(monkeypatch, status):
    calls = []

    @contextmanager
    def scope():
        calls.append(1)
        yield SimpleNamespace(scalar=lambda query: status)

    monkeypatch.setattr(cancellation, "session_scope", scope)
    return calls


def test_no_check_outside_a_search(monkeypatch):
    calls = fake_status(monkeypatch, "cancelled")
    check_cancelled()  # ligne de commande : aucune recherche courante
    assert calls == []


def test_running_search_continues(monkeypatch):
    fake_status(monkeypatch, "running")
    with search_context(1):
        check_cancelled()


@pytest.mark.parametrize("status", ["cancelled", "failed", None])
def test_stopped_search_raises(monkeypatch, status):
    fake_status(monkeypatch, status)
    with search_context(2), pytest.raises(SearchCancelled):
        check_cancelled()


def test_checks_are_throttled(monkeypatch):
    calls = fake_status(monkeypatch, "running")
    with search_context(3):
        check_cancelled()
        check_cancelled()
        check_cancelled(min_interval=0)
    assert len(calls) == 2
