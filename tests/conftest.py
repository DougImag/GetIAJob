"""Réglages communs aux tests : aucun réglage d'administration lu en base."""

from __future__ import annotations

import os

import pytest

from getiajob.services import llm_settings


@pytest.fixture(autouse=True)
def no_admin_settings(monkeypatch):
    """LLM « default » (variables LLM_* des réglages simulés), sans limite de tokens, sans base."""
    monkeypatch.setattr(llm_settings, "_read", lambda key: None)


@pytest.fixture(autouse=True)
def no_env_llm_profiles(monkeypatch):
    """Configurations de LLM du .env de la machine (LLM_PROFILES__*) ignorées : chaque test déclare
    les siennes (sinon un bloc DEFAULT local remplacerait les variables LLM_* des tests)."""
    for name in list(os.environ):
        if name.upper().startswith("LLM_PROFILES__"):
            monkeypatch.delenv(name)
