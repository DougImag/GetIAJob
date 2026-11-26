"""Suivi des tokens du LLM : lecture de l'usage, coût, rattachement à l'utilisateur et à l'étape."""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from getiajob.config import Settings
from getiajob.services import llm_settings, monitoring, usage


def _message(n_in: int = 100, n_out: int = 40, reasoning: int = 0, content: str = "{}") -> AIMessage:
    meta = {"input_tokens": n_in, "output_tokens": n_out, "total_tokens": n_in + n_out}
    if reasoning:
        meta["output_token_details"] = {"reasoning": reasoning}
    return AIMessage(content=content, usage_metadata=meta)


@pytest.fixture
def saved(monkeypatch):
    """Lignes LLMUsage « enregistrées » (sans base) ; réglages : modèle distant tarifé."""
    rows: list = []

    class FakeSession:
        def add(self, obj):
            rows.append(obj)

    @contextmanager
    def fake_scope():
        yield FakeSession()

    settings = Settings(
        llm_provider="openai", llm_model="Olala-v1.0-mini-7A1B", llm_price_input=0.5, llm_price_output=2.0
    )
    monkeypatch.setattr(usage, "session_scope", fake_scope)
    monkeypatch.setattr(llm_settings, "get_settings", lambda: settings)
    monkeypatch.setattr(monitoring, "record", lambda *a, **k: None)
    return rows


def test_tokens_reads_usage_metadata():
    assert usage.tokens(_message(120, 30, reasoning=20)) == (120, 30, 20)
    assert usage.tokens(AIMessage(content="x")) is None


def test_cost_per_million(saved):
    assert usage.cost(1_000_000, 500_000) == pytest.approx(0.5 + 1.0)


def test_cost_none_without_prices(monkeypatch):
    unpriced = Settings(llm_price_input=None, llm_price_output=None)
    monkeypatch.setattr(llm_settings, "get_settings", lambda: unpriced)
    assert usage.cost(1000, 1000) is None


def test_record_attributes_user_and_step(saved):
    with usage.for_user(7), monitoring.timed("llm", "evaluation"):
        usage.record_message(_message(1000, 200, reasoning=50))
    usage.record_message(_message())  # hors contexte : sans utilisateur, étape « autre »
    first, second = saved
    assert (first.user_id, first.step, first.model, first.provider) == (
        7,
        "evaluation",
        "Olala-v1.0-mini-7A1B",
        "openai",
    )
    assert (first.input_tokens, first.output_tokens, first.reasoning_tokens) == (1000, 200, 50)
    assert first.cost == pytest.approx((1000 * 0.5 + 200 * 2.0) / 1e6)
    assert (second.user_id, second.step) == (None, "autre")


def test_no_usage_metadata_records_nothing(saved):
    usage.record_message(AIMessage(content="{}"))
    assert saved == []


def test_record_never_raises(monkeypatch):
    @contextmanager
    def broken():
        raise RuntimeError("base indisponible")
        yield

    monkeypatch.setattr(usage, "session_scope", broken)
    usage.record_message(_message())  # ne lève pas


def test_user_propagates_to_worker_threads(saved):
    """Les extractions du CV tournent dans un pool : le contexte copié garde l'utilisateur."""
    with usage.for_user(3), ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, usage.record_message, _message()) for _ in range(2)
        ]
        [f.result() for f in futures]
    assert [r.user_id for r in saved] == [3, 3]


def test_structured_records_tokens_even_if_json_invalid(saved, monkeypatch):
    from langchain_core.runnables import RunnableLambda

    import getiajob.llm as llm

    monkeypatch.setattr(llm_settings, "get_settings", lambda: Settings(llm_provider="openai"))

    class Out(BaseModel):
        items: list[str]

    fake = RunnableLambda(lambda _: _message(10, 5, content="pas du JSON"))
    fake.bind = lambda **kw: fake  # type: ignore[method-assign]
    monkeypatch.setattr(llm, "get_chat_model", lambda *a: fake)
    with pytest.raises(ValueError):
        llm.structured(Out).invoke("x")
    assert [(r.input_tokens, r.output_tokens) for r in saved] == [(10, 5)]


def test_usage_table_totals_and_top_users():
    from getiajob.web.views.admin_monitoring import _sum_rows

    first = {"email": "Utilisateur", "offres_evaluees": "Offres évaluées"}
    rows = [
        {
            "email": f"u{i}",
            "offres_evaluees": i,
            "appels": 2,
            "envoyes": 100,
            "generes": 10,
            "raisonnement": 0,
            "cout": 0.5 if i % 2 else None,
        }
        for i in range(13)
    ]
    total = _sum_rows(rows, first, "Total")
    assert (total["email"], total["offres_evaluees"], total["appels"], total["envoyes"]) == (
        "Total",
        78,
        26,
        1300,
    )
    assert total["cout"] == 3.0  # 6 lignes tarifées
    others = _sum_rows(rows[10:], first, "Autres utilisateurs (3)")
    assert (others["appels"], others["cout"]) == (6, 0.5)
    assert _sum_rows([r | {"cout": None} for r in rows], first, "Total")["cout"] is None


def test_month_tokens_never_raises(monkeypatch):
    def broken():
        raise RuntimeError("base indisponible")

    monkeypatch.setattr(usage, "session_scope", broken)
    assert usage.month_tokens(1) == 0
