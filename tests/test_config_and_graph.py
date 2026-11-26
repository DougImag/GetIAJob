from pathlib import Path

from getiajob.config import load_preferences
from getiajob.graph import _after_cv, build_graph

ROOT = Path(__file__).parents[1]


def test_default_preferences_file_is_valid():
    prefs = load_preferences(ROOT / "config" / "preferences.yaml")
    assert prefs.search.days == 7
    assert prefs.ranking.top_n >= 1


def test_missing_preferences_file_gives_defaults(tmp_path):
    prefs = load_preferences(tmp_path / "absent.yaml")
    assert prefs.search.locations == ["France"]


def test_graph_compiles_and_routes():
    graph = build_graph()
    assert {"analyze_cv", "scrape_jobs", "rank_jobs"} <= set(graph.get_graph().nodes)
    assert _after_cv({"skip_scrape": True}) == "rank_jobs"
    assert _after_cv({}) == "scrape_jobs"


def test_graph_can_stop_before_ranking():
    """Interface web : collecte seule, l'évaluation par le LLM attend la confirmation."""
    nodes = set(build_graph(rank=False).get_graph().nodes)
    assert {"analyze_cv", "scrape_jobs"} <= nodes and "rank_jobs" not in nodes


def test_read_only_guard_detects_writes():
    from getiajob.db.session import is_write

    for sql in (
        "INSERT INTO t VALUES (1)",
        "  update users set x=1",
        "DELETE FROM t",
        "TRUNCATE t",
        "ALTER TABLE t",
    ):
        assert is_write(sql), sql
    for sql in ("SELECT 1", "WITH x AS (SELECT 1) SELECT * FROM x", "select pg_try_advisory_lock(1)"):
        assert not is_write(sql), sql
