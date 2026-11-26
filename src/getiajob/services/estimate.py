"""Estimation des tokens (et du coût) d'une analyse de CV ou d'une recherche, avant de la lancer.

- Tokens envoyés : calculés à partir des textes réellement envoyés (consignes, CV, profil, offres),
  avec le ratio caractères / token mesuré sur les derniers appels du modèle configuré (colonne
  `llm_usage.input_chars`), ou `CHARS_PER_TOKEN` tant qu'il n'y a pas de mesure.
- Tokens générés : moyenne des derniers appels du même modèle pour la même étape (table `llm_usage`,
  raisonnement compris), rapportée à la taille de l'entrée ; à défaut d'historique, des valeurs
  mesurées sur des CV réels (sans raisonnement : estimation basse pour un modèle qui raisonne).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import lru_cache

from pydantic import BaseModel
from sqlalchemy import text

from getiajob.db import get_engine
from getiajob.services.llm_settings import current
from getiajob.services.usage import cost

CHARS_PER_TOKEN = 3.5  # sans mesure : prudent (qwen2.5 : ~4,4 caractères par token en français)
CALIBRATION_SIZE = 200  # derniers appels utilisés pour mesurer le ratio caractères / token
MESSAGE_OVERHEAD = 8  # balises de rôle du modèle de conversation, par message
MIN_HISTORY = 3  # appels mesurés nécessaires pour se fier à l'historique
HISTORY_SIZE = 30

# Sans historique : tokens générés / tokens envoyés, par étape (mesuré avec qwen2.5:7b sur des CV réels)
DEFAULT_OUTPUT_RATIO = {
    "cv_identity": 0.10,
    "cv_experiences": 0.60,
    "cv_education": 0.20,
    "cv_projects": 0.15,
    "cv_skills": 0.25,
    "cv_synthese": 0.10,
    "evaluation": 0.15,
    "plan_requetes": 0.10,
}
SYNTHESIS_INPUT_RATIO = 1.3  # profil structuré (JSON) envoyé à la synthèse ≈ 1,3 × le texte du CV
OFFER_HEADER_CHARS = 200  # titre, entreprise, lieu, salaire, critères
DEFAULT_OFFER_CHARS = 3500
EXPECTATIONS_TOKENS = 150  # attentes du candidat (fiche de recherche)


@lru_cache(maxsize=8)
def _measured_ratio(model: str, minute: int) -> float:
    """Caractères par token mesurés pour `model` (recalculé au plus une fois par minute)."""
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT sum(input_chars)::float / nullif(sum(input_tokens), 0) AS ratio, count(*) AS n
                    FROM (
                        SELECT input_chars, input_tokens FROM llm_usage
                        WHERE model = :model AND input_chars > 0 AND input_tokens > 0
                        ORDER BY id DESC LIMIT :size
                    ) recent
                    """
                ),
                {"model": model, "size": CALIBRATION_SIZE},
            ).one()
    except Exception:  # base indisponible, colonne absente : ratio par défaut
        return CHARS_PER_TOKEN
    if row.n < MIN_HISTORY or not row.ratio:
        return CHARS_PER_TOKEN
    return min(8.0, max(1.5, float(row.ratio)))


def chars_per_token() -> float:
    return _measured_ratio(current().model, int(time.time() // 60))


def count_tokens(content: str) -> int:
    return round(len(content) / chars_per_token())


@dataclass
class Estimate:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    measured: bool = True  # False : au moins une étape sans historique (valeurs par défaut)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost(self) -> float | None:
        return cost(self.input_tokens, self.output_tokens)

    def __add__(self, other: Estimate) -> Estimate:
        return Estimate(
            self.calls + other.calls,
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.measured and other.measured,
        )

    def times(self, n: int) -> Estimate:
        return Estimate(self.calls * n, self.input_tokens * n, self.output_tokens * n, self.measured)


def history(steps: list[str]) -> dict[str, tuple[float, float]]:
    """{étape : (tokens envoyés moyens, générés moyens)} des derniers appels du modèle configuré."""
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT step, avg(input_tokens) AS n_in, avg(output_tokens) AS n_out, count(*) AS n
                    FROM (
                        SELECT step, input_tokens, output_tokens,
                               row_number() OVER (PARTITION BY step ORDER BY id DESC) AS rank
                        FROM llm_usage
                        WHERE model = :model AND step = ANY(:steps) AND input_tokens > 0
                    ) recent
                    WHERE rank <= :size GROUP BY step
                    """
                ),
                {"model": current().model, "steps": steps, "size": HISTORY_SIZE},
            ).all()
    except Exception:  # table absente, base indisponible : estimation par défaut
        return {}
    return {r.step: (float(r.n_in), float(r.n_out)) for r in rows if r.n >= MIN_HISTORY}


def _schema_tokens(schema: type[BaseModel]) -> int:
    """Schéma JSON compté dans l'entrée seulement s'il est écrit dans le prompt (mode json_object).

    Avec Ollama (`format`) ou `json_schema`, il contraint la génération sans être envoyé comme texte.
    """
    p = current()
    if p.provider == "openai" and p.json_mode == "json_object":
        from getiajob.llm import bounded_schema

        return count_tokens(json.dumps(bounded_schema(schema), ensure_ascii=False)) + 30
    return 0


def messages_tokens(messages: list[tuple[str, str]], schema: type[BaseModel]) -> int:
    """Tokens envoyés pour ces messages (+ le schéma s'il est écrit dans le prompt)."""
    return sum(count_tokens(content) + MESSAGE_OVERHEAD for _, content in messages) + _schema_tokens(schema)


def _call(step: str, n_in: int, past: dict[str, tuple[float, float]]) -> Estimate:
    if step in past:
        avg_in, avg_out = past[step]
        return Estimate(1, n_in, round(n_in * avg_out / avg_in), measured=True)
    return Estimate(1, n_in, round(n_in * DEFAULT_OUTPUT_RATIO.get(step, 0.2)), measured=False)


def cv_analysis(cv_text: str) -> Estimate:
    """Analyse d'un CV : 5 extractions (le CV entier à chaque fois) puis la synthèse du profil."""
    from getiajob.agents.cv_analyzer import COMMON_RULES, SYNTHESIS_PROMPT, TASKS
    from getiajob.schemas import ProfileSynthesis

    steps = [f"cv_{name}" for name in TASKS] + ["cv_synthese"]
    past = history(steps)
    cv_tokens = count_tokens(f"Texte du CV :\n\n{cv_text}")
    total = Estimate()
    for name, (schema, instruction) in TASKS.items():
        n_in = count_tokens(f"{COMMON_RULES}\n\n{instruction}") + cv_tokens + _schema_tokens(schema)
        total += _call(f"cv_{name}", n_in + 2 * MESSAGE_OVERHEAD, past)
    if "cv_synthese" in past:  # entrée = profil extrait, inconnu avant l'analyse : moyenne mesurée
        avg_in, avg_out = past["cv_synthese"]
        total += Estimate(1, round(avg_in), round(avg_out))
    else:
        n_in = (
            count_tokens(SYNTHESIS_PROMPT)
            + round(cv_tokens * SYNTHESIS_INPUT_RATIO)
            + _schema_tokens(ProfileSynthesis)
            + 2 * MESSAGE_OVERHEAD
        )
        total += _call("cv_synthese", n_in, past)
    return total


def average_offer_chars() -> int:
    """Taille moyenne d'une offre telle qu'envoyée au LLM (description coupée à 6 000 caractères)."""
    try:
        with get_engine().connect() as conn:
            value = conn.execute(
                text(
                    "SELECT avg(least(length(coalesce(description, '')), 6000)) FROM "
                    "(SELECT description FROM job_offers ORDER BY id DESC LIMIT 500) recent"
                )
            ).scalar()
    except Exception:
        value = None
    return round(value or DEFAULT_OFFER_CHARS) + OFFER_HEADER_CHARS


@dataclass
class SearchEstimate:
    per_offer: Estimate
    max_offers: int
    planning: Estimate  # génération des requêtes (vide si requêtes saisies ou sites non interrogés)
    already_assessed: int  # offres déjà évaluées pour ce CV (non refacturées si critères inchangés)

    @property
    def maximum(self) -> Estimate:
        return self.per_offer.times(self.max_offers) + self.planning


def search(
    profile_text: str, max_offers: int, plan_queries: bool, already_assessed: int = 0
) -> SearchEstimate:
    """Recherche : au plus `max_offers` évaluations (offres les plus proches du CV) + les requêtes."""
    from getiajob.agents.job_scraper import PLANNER_PROMPT
    from getiajob.agents.ranker import ASSESS_PROMPT
    from getiajob.schemas import JobAssessment, SearchPlan

    past = history(["evaluation", "plan_requetes"])
    profile_tokens = count_tokens(profile_text)
    n_in = (
        count_tokens(ASSESS_PROMPT)
        + profile_tokens
        + EXPECTATIONS_TOKENS
        + round(average_offer_chars() / chars_per_token())
        + _schema_tokens(JobAssessment)
        + 2 * MESSAGE_OVERHEAD
    )
    planning = Estimate()
    if plan_queries:
        planning = _call(
            "plan_requetes",
            count_tokens(PLANNER_PROMPT)
            + profile_tokens
            + EXPECTATIONS_TOKENS
            + _schema_tokens(SearchPlan)
            + 2 * MESSAGE_OVERHEAD,
            past,
        )
    return SearchEstimate(_call("evaluation", n_in, past), max_offers, planning, already_assessed)


def assessed_offers(cv_id: int) -> int:
    try:
        with get_engine().connect() as conn:
            return int(
                conn.execute(
                    text("SELECT count(*) FROM job_matches WHERE cv_id = :cv AND llm_score IS NOT NULL"),
                    {"cv": cv_id},
                ).scalar()
                or 0
            )
    except Exception:
        return 0


def evaluation(candidates: list[dict], limit: int) -> Estimate:
    """Évaluation des `limit` premières offres candidates (cf. `ranker.preview_assessment`) :
    seules celles marquées « à évaluer » sont envoyées au LLM, les autres ont une note en cache."""
    past = history(["evaluation"])
    total = Estimate()
    for candidate in candidates[:limit]:
        if candidate["eval"]:
            total += _call("evaluation", candidate["in"], past)
    return total
