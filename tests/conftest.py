"""Réglages communs aux tests : aucun réglage d'administration lu en base."""

from __future__ import annotations

import pytest

from getiajob.services import llm_settings


@pytest.fixture(autouse=True)
def no_admin_settings(monkeypatch):
    """LLM « default » (variables LLM_* des réglages simulés), sans limite de tokens, sans base."""
    monkeypatch.setattr(llm_settings, "_read", lambda key: None)
