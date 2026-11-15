"""CV d'un utilisateur : liste, téléchargement, suppression et analyse en tâche de fond.

L'extraction elle-même est dans l'agent 1 (`agents.cv_analyzer`). Ici, on gère le statut de
l'analyse (pending → running → done / failed) pour qu'elle survive à un changement de page,
d'onglet ou de fenêtre : elle tourne dans un thread du serveur, pas dans la session du navigateur.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import undefer

from getiajob.config import get_settings
from getiajob.db import CVRecord, JobMatch, MetricEvent, session_scope
from getiajob.schemas import CVProfile

log = logging.getLogger(__name__)

ACTIVE_STATUSES = ("pending", "running")


@dataclass(frozen=True)
class CVSummary:
    id: int
    file_name: str
    created_at: datetime
    profile: CVProfile | None
    analysis_status: str | None = None
    analysis_error: str | None = None

    @property
    def analyzing(self) -> bool:
        return self.analysis_status in ACTIVE_STATUSES

    @property
    def label(self) -> str:
        if self.analyzing:
            status = "analyse en cours"
        elif self.profile is None:
            status = "non analysé"
        elif self.profile.is_outdated:
            status = "analyse à refaire"
        else:
            status = self.profile.headline or "analysé"
        return f"{self.file_name} — {status} ({self.created_at:%d/%m/%Y})"


def list_cvs(user_id: int) -> list[CVSummary]:
    with session_scope() as session:
        rows = session.scalars(
            select(CVRecord).where(CVRecord.user_id == user_id).order_by(CVRecord.created_at.desc())
        ).all()
        return [
            CVSummary(
                id=r.id,
                file_name=r.file_name,
                created_at=r.created_at,
                profile=CVProfile.model_validate(r.profile) if r.profile else None,
                analysis_status=r.analysis_status,
                analysis_error=r.analysis_error,
            )
            for r in rows
        ]


def get_cv(user_id: int, cv_id: int) -> CVSummary | None:
    return next((cv for cv in list_cvs(user_id) if cv.id == cv_id), None)


def analysis_status(cv_id: int) -> tuple[str | None, str | None]:
    """(statut, erreur) de l'analyse — requête légère, appelée en boucle par l'interface."""
    with session_scope() as session:
        row = session.execute(
            select(CVRecord.analysis_status, CVRecord.analysis_error).where(CVRecord.id == cv_id)
        ).first()
        return (row[0], row[1]) if row else (None, None)


def request_analysis(cv_id: int) -> bool:
    """Marque le CV « en attente d'analyse ». Renvoie False si une analyse est déjà en cours."""
    with session_scope() as session:
        result = session.execute(
            update(CVRecord)
            .where(
                CVRecord.id == cv_id,
                (CVRecord.analysis_status.is_(None)) | (CVRecord.analysis_status.not_in(ACTIVE_STATUSES)),
            )
            .values(analysis_status="pending", analysis_error=None)
        )
        return result.rowcount > 0


def _set_status(cv_id: int, **values) -> None:
    with session_scope() as session:
        session.execute(update(CVRecord).where(CVRecord.id == cv_id).values(**values))


class _ProgressTracker:
    """Enregistre en base l'état de chaque étape (appelé depuis plusieurs threads)."""

    def __init__(self, cv_id: int, steps: list[str], workers: int) -> None:
        self.cv_id = cv_id
        self.lock = threading.Lock()
        self.state = {"workers": workers, "steps": dict.fromkeys(steps, "pending"), "started": {}}
        self._save()

    def _save(self) -> None:
        try:
            _set_status(self.cv_id, analysis_progress=self.state)
        except Exception:  # l'avancement est informatif : il ne doit jamais faire échouer l'analyse
            log.debug("Avancement de l'analyse non enregistré", exc_info=True)

    def __call__(self, step: str, state: str) -> None:
        with self.lock:
            self.state["steps"][step] = state
            if state == "running":
                self.state["started"][step] = datetime.now(UTC).isoformat()
            self._save()


def run_cv_analysis(cv_id: int, force: bool = True) -> None:
    """Exécuté dans un thread du serveur : analyse le CV et enregistre le statut."""
    from getiajob.agents.cv_analyzer import STEPS, ensure_profile

    _set_status(
        cv_id, analysis_status="running", analysis_started_at=datetime.now(UTC), analysis_progress=None
    )
    tracker = _ProgressTracker(cv_id, list(STEPS), max(1, get_settings().llm_concurrency))
    try:
        ensure_profile(cv_id, force=force, progress=tracker)
        _set_status(cv_id, analysis_status="done", analysis_error=None, analysis_progress=None)
    except Exception as exc:
        log.exception("Échec de l'analyse du CV %s", cv_id)
        _set_status(cv_id, analysis_status="failed", analysis_error=str(exc)[:2000])


# --------------------------------------------------------------------------- #
# Avancement et estimation du temps restant
# --------------------------------------------------------------------------- #

HISTORY_SIZE = 5  # durées récentes prises en compte par étape
DEFAULT_EMBEDDING_S = 5.0  # indexation (embedding) jamais mesurée


@dataclass(frozen=True)
class AnalysisProgress:
    status: str | None  # pending, running…
    steps: list[tuple[str, str, str]]  # (clé, libellé, état : pending / running / done / failed)
    elapsed_s: float | None
    remaining_s: float | None  # None : pas encore d'historique pour estimer

    @property
    def done(self) -> int:
        return sum(state in ("done", "failed") for _, _, state in self.steps)

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def running(self) -> list[str]:
        return [label for _, label, state in self.steps if state == "running"]


def step_durations() -> dict[str, float]:
    """Durée moyenne (s) de chaque étape, sur les dernières mesures réussies."""
    from getiajob.agents.cv_analyzer import STEPS

    wanted = {metric: key for key, (_, metric) in STEPS.items()}
    samples: dict[str, list[int]] = {}
    with session_scope() as session:
        rows = session.execute(
            select(MetricEvent.kind, MetricEvent.name, MetricEvent.duration_ms)
            .where(
                MetricEvent.ok.is_(True),
                MetricEvent.duration_ms.is_not(None),
                MetricEvent.kind.in_({kind for kind, _ in wanted}),
                MetricEvent.name.in_({name for _, name in wanted}),
            )
            .order_by(MetricEvent.id.desc())
            .limit(300)
        ).all()
    for kind, name, ms in rows:
        key = wanted.get((kind, name))
        if key and len(samples.setdefault(key, [])) < HISTORY_SIZE:
            samples[key].append(ms)
    return {key: sum(v) / len(v) / 1000 for key, v in samples.items() if v}


def estimate_remaining(
    steps: dict[str, str], started: dict[str, datetime], workers: int, avg: dict[str, float], now: datetime
) -> float | None:
    """Temps restant estimé (s) : étapes parallèles réparties sur `workers`, puis étapes successives."""
    from getiajob.agents.cv_analyzer import PARALLEL_STEPS

    llm_known = [v for k, v in avg.items() if k != "embedding"]
    if not llm_known:
        return None  # aucune analyse mesurée : pas d'estimation possible
    # Étape jamais mesurée : durée moyenne des étapes LLM connues (indexation : quelques secondes)
    fallback = {"embedding": DEFAULT_EMBEDDING_S}

    def duration(key: str) -> float:
        return avg.get(key, fallback.get(key, sum(llm_known) / len(llm_known)))

    def left(key: str) -> float:
        state = steps.get(key, "pending")
        if state in ("done", "failed"):
            return 0.0
        if state == "running" and key in started:
            return max(duration(key) - (now - started[key]).total_seconds(), 0.0)
        return duration(key)

    parts = {key: left(key) for key in steps}
    parallel = sum(parts[k] for k in steps if k in PARALLEL_STEPS) / max(1, workers)
    return parallel + sum(parts[k] for k in steps if k not in PARALLEL_STEPS)


def analysis_progress(cv_id: int) -> AnalysisProgress:
    from getiajob.agents.cv_analyzer import STEPS

    with session_scope() as session:
        status, started_at, raw = session.execute(
            select(CVRecord.analysis_status, CVRecord.analysis_started_at, CVRecord.analysis_progress).where(
                CVRecord.id == cv_id
            )
        ).one()
    now = datetime.now(UTC)
    raw = raw or {}
    states: dict[str, str] = raw.get("steps") or {}
    steps = [(key, label, states.get(key, "pending")) for key, (label, _) in STEPS.items()]
    elapsed = (now - started_at).total_seconds() if status == "running" and started_at else None
    remaining = None
    if status == "running" and states:
        started = {k: datetime.fromisoformat(v) for k, v in (raw.get("started") or {}).items()}
        remaining = estimate_remaining(states, started, raw.get("workers") or 1, step_durations(), now)
    return AnalysisProgress(status, steps, elapsed, remaining)


def update_profile(user_id: int, cv_id: int, profile: CVProfile) -> None:
    """Enregistre un profil corrigé à la main.

    L'embedding du CV est remis à zéro (recalculé à la prochaine recherche) et les notes déjà
    calculées pour ce CV sont supprimées : elles reposaient sur l'ancien profil.
    """
    profile = profile.model_copy(update={"edited_at": datetime.now(UTC).isoformat(timespec="seconds")})
    with session_scope() as session:
        record = session.scalar(select(CVRecord).where(CVRecord.id == cv_id, CVRecord.user_id == user_id))
        if record is None:
            raise ValueError("CV introuvable")
        record.profile = profile.model_dump(mode="json")
        record.embedding = None
        session.execute(delete(JobMatch).where(JobMatch.cv_id == cv_id))


def get_cv_file(user_id: int, cv_id: int) -> tuple[str, bytes] | None:
    with session_scope() as session:
        record = session.scalar(
            select(CVRecord)
            .options(undefer(CVRecord.file_data))
            .where(CVRecord.id == cv_id, CVRecord.user_id == user_id)
        )
        if record is None or record.file_data is None:
            return None
        return record.file_name, record.file_data


def get_cv_text(user_id: int, cv_id: int) -> str | None:
    with session_scope() as session:
        return session.scalar(
            select(CVRecord.raw_text).where(CVRecord.id == cv_id, CVRecord.user_id == user_id)
        )


def delete_cv(user_id: int, cv_id: int) -> bool:
    with session_scope() as session:
        record = session.scalar(select(CVRecord).where(CVRecord.id == cv_id, CVRecord.user_id == user_id))
        if record is None:
            return False
        session.delete(record)
        return True


def owns_cv(user_id: int, cv_id: int) -> bool:
    with session_scope() as session:
        return (
            session.scalar(select(CVRecord.id).where(CVRecord.id == cv_id, CVRecord.user_id == user_id))
            is not None
        )
