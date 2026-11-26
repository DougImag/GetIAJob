"""Configurations de LLM (.env), configuration active et limite de tokens (sans base)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from getiajob.config import Settings, llm_profiles
from getiajob.services import llm_settings
from getiajob.services.llm_settings import TokenBudgetExceeded, TokenLimit


def _settings(monkeypatch, **extra) -> Settings:
    s = Settings(**({"llm_provider": "ollama", "llm_model": "qwen2.5:7b"} | extra))
    monkeypatch.setattr(llm_settings, "get_settings", lambda: s)
    return s


def test_profiles_from_nested_env(monkeypatch):
    monkeypatch.setenv("LLM_PROFILES__OLALA__MODEL", "Olala-v1.0-mini-7A1B")
    monkeypatch.setenv("LLM_PROFILES__OLALA__API_KEY", "secret")
    monkeypatch.setenv("LLM_PROFILES__OLALA__PRICE_INPUT", "0")
    profiles = llm_profiles(Settings(llm_provider="ollama", llm_model="qwen2.5:7b"))
    assert list(profiles) == ["default", "olala"]
    assert (profiles["default"].provider, profiles["default"].model) == ("ollama", "qwen2.5:7b")
    olala = profiles["olala"]
    assert (olala.name, olala.provider, olala.model, olala.api_key) == (
        "olala",
        "openai",
        "Olala-v1.0-mini-7A1B",
        "secret",
    )
    assert olala.price_input == 0 and "oai.endpoints" in olala.title


def test_current_follows_admin_choice(monkeypatch):
    monkeypatch.setenv("LLM_PROFILES__OLALA__MODEL", "Olala-v1.0-mini-7A1B")
    _settings(monkeypatch)
    assert llm_settings.current().name == "default"
    choose = lambda name: lambda key: {"name": name} if key == "llm_profile" else None  # noqa: E731
    monkeypatch.setattr(llm_settings, "_read", choose("olala"))
    assert llm_settings.current().model == "Olala-v1.0-mini-7A1B"
    # Configuration retirée du .env : retour à « default »
    monkeypatch.setattr(llm_settings, "_read", choose("absente"))
    assert llm_settings.current().name == "default"


def test_period_start_local_midnight_and_month():
    now = datetime(2026, 11, 26, 9, 30, tzinfo=UTC)  # 10 h 30 à Paris
    day = llm_settings.period_start("day", now)
    month = llm_settings.period_start("month", now)
    assert (day.day, day.hour, month.day, month.hour) == (26, 0, 1, 0)
    assert llm_settings.period_start("total", now) is None


def _limit(monkeypatch, limit: TokenLimit, used: int, provider: str = "openai"):
    _settings(monkeypatch, llm_provider=provider)
    monkeypatch.setattr(llm_settings, "token_limit", lambda: limit)
    monkeypatch.setattr(llm_settings, "used_tokens", lambda lim: used)


def test_check_budget_blocks_when_exhausted(monkeypatch):
    _limit(monkeypatch, TokenLimit(1000, "month"), used=1000)
    with pytest.raises(TokenBudgetExceeded, match="Admin › Paramètres"):
        llm_settings.check_budget()


def test_check_budget_lets_through_under_limit(monkeypatch):
    _limit(monkeypatch, TokenLimit(1000, "day"), used=999)
    llm_settings.check_budget()
    assert llm_settings.budget().remaining == 1


def test_ollama_not_limited_unless_counted(monkeypatch):
    _limit(monkeypatch, TokenLimit(1000, "month"), used=5000, provider="ollama")
    llm_settings.check_budget()  # Ollama non compté : pas de blocage
    assert llm_settings.budget().remaining is None
    _limit(monkeypatch, TokenLimit(1000, "month", include_local=True), used=5000, provider="ollama")
    assert llm_settings.budget().exhausted


def test_llm_call_refused_when_budget_exhausted(monkeypatch):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from pydantic import BaseModel

    from getiajob import llm

    calls = []
    fake = RunnableLambda(lambda _: calls.append(1) or AIMessage(content='{"a": "x"}'))
    fake.bind = lambda **kw: fake  # type: ignore[method-assign]
    monkeypatch.setattr(llm, "get_chat_model", lambda *a: fake)
    _limit(monkeypatch, TokenLimit(10, "total"), used=10)

    class Out(BaseModel):
        a: str

    with pytest.raises(TokenBudgetExceeded):
        llm.structured(Out).invoke("x")
    assert calls == []  # le LLM n'a pas été appelé


def test_cost_at_limit_projects_remaining_tokens(monkeypatch):
    _settings(monkeypatch, llm_provider="openai", llm_price_input=1.0, llm_price_output=3.0)
    usage = llm_settings.PeriodUsage(input_tokens=600_000, output_tokens=200_000, cost=1.2)
    limit = TokenLimit(1_800_000, "month")
    # 1 M de tokens restants, 75 % envoyés à 1 € et 25 % générés à 3 € → 1,5 € de plus
    assert llm_settings.cost_at_limit(limit, usage) == pytest.approx(1.2 + 1.5)


def test_cost_at_limit_without_prices(monkeypatch):
    # Prix explicitement vides : indépendant des LLM_PRICE_* du .env de la machine
    _settings(monkeypatch, llm_provider="openai", llm_price_input=None, llm_price_output=None)
    usage = llm_settings.PeriodUsage(1000, 100, cost=None)
    assert llm_settings.cost_at_limit(TokenLimit(10_000, "day"), usage) is None
    assert llm_settings.cost_at_limit(TokenLimit(), usage) is None


def test_user_limit_blocks_only_that_user(monkeypatch):
    from getiajob.services import usage

    _limit(monkeypatch, TokenLimit(), used=0)  # pas de limite globale
    monkeypatch.setattr(llm_settings, "user_limit", lambda uid: 1000 if uid == 7 else None)
    monkeypatch.setattr(usage, "month_tokens", lambda uid: 1200)
    with usage.for_user(7), pytest.raises(TokenBudgetExceeded, match="Admin › Utilisateurs"):
        llm_settings.check_budget()
    with usage.for_user(8):
        llm_settings.check_budget()  # pas de limite propre
    llm_settings.check_budget()  # appel hors utilisateur (ligne de commande)


def test_budget_status_keeps_tightest_limit(monkeypatch):
    from getiajob.web.views import admin_format

    _limit(monkeypatch, TokenLimit(10_000_000, "month"), used=1_000_000)
    monkeypatch.setattr(
        llm_settings, "user_budget", lambda uid: llm_settings.UserBudget(500_000, 450_000) if uid else None
    )
    msg, level = admin_format.budget_status(100_000, user_id=3)
    assert level == "warn" and "ta limite" in msg and "50,0 k" in msg
    msg, level = admin_format.budget_status(100_000)
    assert level == "ok" and "limite globale" in msg
    monkeypatch.setattr(llm_settings, "user_budget", lambda uid: llm_settings.UserBudget(500_000, 600_000))
    assert admin_format.budget_status(1, user_id=3)[1] == "block"


def test_default_profile_can_be_declared_as_a_block(monkeypatch):
    monkeypatch.setenv("LLM_PROFILES__DEFAULT__LABEL", "Qwen local")
    monkeypatch.setenv("LLM_PROFILES__DEFAULT__PROVIDER", "ollama")
    monkeypatch.setenv("LLM_PROFILES__DEFAULT__MODEL", "qwen2.5:7b")
    monkeypatch.setenv("LLM_PROFILES__GEMMA4__PROVIDER", "ollama")
    monkeypatch.setenv("LLM_PROFILES__GEMMA4__MODEL", "gemma4:12b")
    # Le bloc DEFAULT remplace les variables LLM_*
    profiles = llm_profiles(Settings(llm_provider="openai", llm_model="ignoré"))
    assert list(profiles)[0] == "default"
    default = profiles["default"]
    assert (default.name, default.title, default.provider, default.model) == (
        "default",
        "Qwen local",
        "ollama",
        "qwen2.5:7b",
    )
    assert profiles["gemma4"].model == "gemma4:12b"


def test_default_block_carries_shared_settings(monkeypatch):
    for field, value in {
        "PROVIDER": "ollama",
        "MODEL": "qwen2.5:7b",
        "BASE_URL": "http://host.docker.internal:11434/",
        "NUM_CTX": "16384",
        "EMBEDDING_MODEL": "bge-m3",
        "EMBEDDING_DIM": "1024",
    }.items():
        monkeypatch.setenv(f"LLM_PROFILES__DEFAULT__{field}", value)
    monkeypatch.setenv("LLM_PROFILES__GEMMA4__PROVIDER", "ollama")
    monkeypatch.setenv("LLM_PROFILES__GEMMA4__MODEL", "gemma4:12b")
    monkeypatch.setenv("LLM_PROFILES__OLALA__MODEL", "Olala-v1.0-mini-7A1B")
    monkeypatch.setenv("LLM_PROFILES__OLALA__NUM_CTX", "4096")
    monkeypatch.setenv("LLM_PROFILES__OLALA__EMBEDDING_MODEL", "ignoré hors du bloc DEFAULT")
    s = Settings(ollama_base_url="http://ollama:11434", llm_num_ctx=8192)
    # Réglages communs : ceux du bloc DEFAULT priment sur OLLAMA_BASE_URL, LLM_NUM_CTX, EMBEDDING_*
    assert (s.ollama_base_url, s.llm_num_ctx, s.embedding_model, s.embedding_dim) == (
        "http://host.docker.internal:11434/",
        16384,
        "bge-m3",
        1024,
    )
    profiles = llm_profiles(s)
    assert (profiles["default"].base_url, profiles["default"].num_ctx) == (
        "http://host.docker.internal:11434",
        16384,
    )
    # Autre modèle Ollama : adresse et contexte communs par défaut
    assert (profiles["gemma4"].base_url, profiles["gemma4"].num_ctx) == (
        "http://host.docker.internal:11434",
        16384,
    )
    # API distante : adresse OVH par défaut, contexte propre
    assert "oai.endpoints" in profiles["olala"].base_url and profiles["olala"].num_ctx == 4096


def test_without_default_block_llm_variables_still_apply():
    s = Settings(
        llm_provider="ollama", llm_model="qwen2.5:3b", ollama_base_url="http://ollama:11434", llm_num_ctx=4096
    )
    default = llm_profiles(s)["default"]
    assert (default.model, default.base_url, default.num_ctx) == ("qwen2.5:3b", "http://ollama:11434", 4096)
