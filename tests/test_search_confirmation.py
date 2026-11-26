"""Pause avant l'évaluation par le LLM : quand la demander, et estimation de l'évaluation."""

from __future__ import annotations

from getiajob.config import Preferences
from getiajob.services import estimate, searches


def _params(prefilter: int = 2) -> dict:
    prefs = Preferences()
    prefs.ranking.prefilter_top_k = prefilter
    return {"preferences": prefs.model_dump(mode="json"), "confirm": True}


def _run_pause(monkeypatch, candidates: list[dict], prefilter: int = 2) -> tuple[bool, dict]:
    updates: dict = {}
    monkeypatch.setattr(searches, "_update", lambda search_id, **values: updates.update(values))

    def preview(cv_id, prefs, job_ids, force):
        return {"n_jobs": 10, "n_kept": len(candidates), "candidates": candidates}

    state = {"cv_id": 1, "job_ids": [5, 6, 7], "force_rank": False}
    paused = searches._pause_for_confirmation(42, _params(prefilter), state, preview)
    return paused, updates


def test_pauses_when_offers_need_the_llm(monkeypatch):
    candidates = [{"job_id": 5, "eval": True, "in": 2000}, {"job_id": 6, "eval": False, "in": 2100}]
    paused, updates = _run_pause(monkeypatch, candidates)
    assert paused
    assert updates["status"] == searches.AWAITING and updates["n_offers"] == 3
    assert updates["params"]["job_ids"] == [5, 6, 7]
    assert updates["params"]["preview"]["candidates"] == candidates


def test_no_pause_when_everything_is_cached(monkeypatch):
    # La 3e offre est à évaluer, mais hors des 2 offres pré-sélectionnées : rien ne sera facturé
    candidates = [
        {"job_id": 5, "eval": False, "in": 2000},
        {"job_id": 6, "eval": False, "in": 2000},
        {"job_id": 7, "eval": True, "in": 2000},
    ]
    paused, updates = _run_pause(monkeypatch, candidates, prefilter=2)
    assert not paused and updates == {}


def test_evaluation_estimate_counts_only_offers_to_assess(monkeypatch):
    monkeypatch.setattr(estimate, "history", lambda steps: {"evaluation": (2000.0, 400.0)})
    candidates = [
        {"job_id": 1, "eval": True, "in": 2000},
        {"job_id": 2, "eval": False, "in": 5000},
        {"job_id": 3, "eval": True, "in": 3000},
    ]
    est = estimate.evaluation(candidates, 3)
    assert (est.calls, est.input_tokens, est.output_tokens) == (2, 5000, 1000)
    assert estimate.evaluation(candidates, 1).calls == 1
