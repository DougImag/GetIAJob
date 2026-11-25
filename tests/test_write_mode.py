import pytest
import streamlit as st

from getiajob.db import READ_ONLY
from getiajob.services.users import AuthError, SessionUser
from getiajob.web import state

ADMIN = SessionUser(id=1, email="admin@example.com", full_name="Admin", is_admin=True)
TARGET = SessionUser(id=2, email="sam@example.com", full_name="Sam")


@pytest.fixture
def impersonating(monkeypatch):
    calls = []
    monkeypatch.setattr(
        state, "impersonation_write_mode", lambda actor, email, enabled: calls.append((actor, email, enabled))
    )
    monkeypatch.setattr(state, "is_admin", lambda user_id: True)
    st.session_state.clear()
    st.session_state[state.IMPERSONATOR_KEY] = ADMIN
    st.session_state[state.USER_KEY] = TARGET
    yield calls
    st.session_state.clear()
    READ_ONLY.set(False)


def test_consultation_starts_read_only(impersonating):
    assert state.apply_read_only() is True
    assert state.is_read_only() and not state.is_write_mode()


def test_write_mode_toggle_is_audited(impersonating):
    state.set_write_mode(True)
    assert state.is_write_mode() and not state.is_read_only()
    assert state.apply_read_only() is False and READ_ONLY.get() is False
    state.set_write_mode(False)
    assert state.is_read_only() and READ_ONLY.get() is True
    assert impersonating == [(1, "sam@example.com", True), (1, "sam@example.com", False)]


def test_write_mode_refused_without_admin_role(impersonating, monkeypatch):
    def refuse(*_):
        raise AuthError("Action réservée aux administrateurs.")

    monkeypatch.setattr(state, "impersonation_write_mode", refuse)
    state.set_write_mode(True)
    assert state.is_read_only()


def test_write_mode_dropped_when_admin_role_revoked(impersonating, monkeypatch):
    state.set_write_mode(True)
    monkeypatch.setattr(state, "is_admin", lambda user_id: False)
    assert state.apply_read_only() is True
    assert not state.is_write_mode()


def test_write_mode_ends_with_consultation(impersonating, monkeypatch):
    monkeypatch.setattr(state, "end_impersonation", lambda *_: None)
    state.set_write_mode(True)
    state.stop_impersonation()
    assert state.current_user() == ADMIN
    assert not state.is_write_mode() and not state.is_read_only()
    assert state.WRITE_MODE_KEY not in st.session_state


def test_no_write_mode_outside_consultation(impersonating):
    st.session_state.pop(state.IMPERSONATOR_KEY)
    state.set_write_mode(True)
    assert not state.is_write_mode()
