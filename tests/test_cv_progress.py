from datetime import UTC, datetime, timedelta

from getiajob.agents.cv_analyzer import PARALLEL_STEPS, STEPS
from getiajob.services.cvs import AnalysisProgress, estimate_remaining

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
AVG = dict.fromkeys(STEPS, 60.0)  # 1 min par étape


def _states(**overrides):
    return {key: overrides.get(key, "pending") for key in STEPS}


def test_steps_cover_parallel_tasks_then_synthesis_and_indexing():
    assert set(PARALLEL_STEPS) < set(STEPS)
    assert list(STEPS)[-2:] == ["synthesis", "embedding"]


def test_nothing_started_parallel_steps_shared_between_workers():
    # 5 extractions de 60 s sur 2 workers = 150 s, puis synthèse + indexation = 120 s
    assert estimate_remaining(_states(), {}, 2, AVG, NOW) == 270


def test_running_step_counts_only_its_remaining_time():
    states = _states(identity="done", experiences="done", education="done", projects="done", skills="done")
    states["synthesis"] = "running"
    started = {"synthesis": NOW - timedelta(seconds=45)}
    assert estimate_remaining(states, started, 2, AVG, NOW) == 15 + 60


def test_overdue_step_never_negative():
    states = _states(**dict.fromkeys(PARALLEL_STEPS, "done"), synthesis="done", embedding="running")
    started = {"embedding": NOW - timedelta(minutes=10)}
    assert estimate_remaining(states, started, 1, AVG, NOW) == 0


def test_no_history_means_no_estimate():
    assert estimate_remaining(_states(), {}, 1, {}, NOW) is None
    assert estimate_remaining(_states(), {}, 1, {"embedding": 3.0}, NOW) is None


def test_missing_steps_use_known_average():
    # Seule « identity » est mesurée (30 s) : les autres étapes LLM valent 30 s, l'indexation 5 s
    assert estimate_remaining(_states(), {}, 1, {"identity": 30.0}, NOW) == 5 * 30 + 30 + 5


def test_progress_counts_done_and_failed_steps():
    steps = [
        (k, label, "done" if k == "identity" else "failed" if k == "skills" else "pending")
        for k, (label, _) in STEPS.items()
    ]
    prog = AnalysisProgress("running", steps, 10.0, None)
    assert (prog.done, prog.total) == (2, len(STEPS))
