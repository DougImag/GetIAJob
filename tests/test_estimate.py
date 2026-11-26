"""Estimation des tokens avant une analyse de CV ou une recherche (sans base)."""

from __future__ import annotations

import pytest

from getiajob.config import Settings
from getiajob.services import estimate, llm_settings

CV = "Jean Dupont — Ingénieur logiciel. " * 170  # ≈ 6 000 caractères, la taille moyenne mesurée


@pytest.fixture
def no_db(monkeypatch):
    """Ni historique ni offres en base ; Ollama sans prix (indépendant du .env de la machine)."""
    settings = Settings(llm_provider="ollama", llm_price_input=None, llm_price_output=None)
    monkeypatch.setattr(estimate, "history", lambda steps: {})
    monkeypatch.setattr(estimate, "chars_per_token", lambda: estimate.CHARS_PER_TOKEN)
    monkeypatch.setattr(estimate, "average_offer_chars", lambda: 3700)
    monkeypatch.setattr(llm_settings, "get_settings", lambda: settings)
    return settings


def test_cv_analysis_without_history(no_db):
    est = estimate.cv_analysis(CV)
    assert est.calls == 6 and not est.measured
    # 5 fois le CV (~1 700 tokens) + consignes, plus la synthèse : dans la fourchette mesurée
    assert 10_000 < est.input_tokens < 16_000
    assert 2_000 < est.output_tokens < 4_500
    assert est.cost is None  # aucun prix configuré


def test_cv_analysis_grows_with_cv(no_db):
    assert estimate.cv_analysis(CV * 2).total > estimate.cv_analysis(CV).total * 1.5


def test_history_scales_output(no_db, monkeypatch):
    # Modèle qui raisonne : 2 tokens générés par token envoyé sur les évaluations passées
    monkeypatch.setattr(estimate, "history", lambda steps: {"evaluation": (3000.0, 6000.0)})
    est = estimate.search("profil " * 200, 30, plan_queries=False)
    assert est.per_offer.measured
    assert est.per_offer.output_tokens == pytest.approx(2 * est.per_offer.input_tokens, abs=1)


def test_search_maximum(no_db):
    est = estimate.search("profil " * 200, 30, plan_queries=True, already_assessed=12)
    assert est.maximum.calls == 31  # 30 évaluations + la génération des requêtes
    assert est.maximum.total == est.per_offer.total * 30 + est.planning.total
    assert 1_500 < est.per_offer.input_tokens < 3_000
    assert est.already_assessed == 12
    assert estimate.search("p", 30, plan_queries=False).planning.calls == 0


def test_cost_with_prices(no_db, monkeypatch):
    priced = Settings(llm_price_input=1.0, llm_price_output=1.0)
    monkeypatch.setattr(llm_settings, "get_settings", lambda: priced)
    est = estimate.cv_analysis(CV)
    assert est.cost == pytest.approx(est.total / 1e6)


def test_schema_counted_only_in_json_object_mode(monkeypatch):
    from getiajob.schemas import JobAssessment

    monkeypatch.setattr(llm_settings, "get_settings", lambda: Settings(llm_provider="openai"))
    assert estimate._schema_tokens(JobAssessment) == 0
    monkeypatch.setattr(
        llm_settings, "get_settings", lambda: Settings(llm_provider="openai", llm_json_mode="json_object")
    )
    assert estimate._schema_tokens(JobAssessment) > 100


def test_measured_ratio_calibrates_input(no_db, monkeypatch):
    """Un modèle qui compte 4,4 caractères par token : moins de tokens estimés qu'avec le défaut."""
    default = estimate.cv_analysis(CV).input_tokens
    monkeypatch.setattr(estimate, "chars_per_token", lambda: 4.4)
    assert estimate.cv_analysis(CV).input_tokens == pytest.approx(default * 3.5 / 4.4, rel=0.03)


def test_input_chars_counts_message_contents():
    from langchain_core.messages import HumanMessage, SystemMessage

    from getiajob.llm import input_chars

    assert input_chars([("system", "abc"), ("human", "de")]) == 5
    assert input_chars([SystemMessage("abc"), HumanMessage("defg")]) == 7
    assert input_chars("hello") == 5
