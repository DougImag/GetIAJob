"""Orchestration LangGraph : analyse CV → collecte des offres → tri."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from getiajob.agents.cv_analyzer import cv_analyzer_node
from getiajob.agents.job_scraper import job_scraper_node
from getiajob.agents.ranker import ranker_node


class PipelineState(TypedDict, total=False):
    # Entrées
    cv_path: str  # CLI : chemin du fichier
    cv_id: int  # Web : CV déjà enregistré en base (sinon sortie de l'agent 1)
    preferences: dict  # Web : préférences de la session (sinon config/preferences.yaml)
    force_cv: bool
    force_rank: bool
    skip_scrape: bool
    # Sorties des agents
    profile: dict
    job_ids: list[int]
    results: list[dict]


def _after_cv(state: PipelineState) -> str:
    return "rank_jobs" if state.get("skip_scrape") else "scrape_jobs"


def build_graph():
    graph = StateGraph(PipelineState)
    graph.add_node("analyze_cv", cv_analyzer_node)
    graph.add_node("scrape_jobs", job_scraper_node)
    graph.add_node("rank_jobs", ranker_node)

    graph.add_edge(START, "analyze_cv")
    graph.add_conditional_edges("analyze_cv", _after_cv, ["scrape_jobs", "rank_jobs"])
    graph.add_edge("scrape_jobs", "rank_jobs")
    graph.add_edge("rank_jobs", END)
    return graph.compile()
