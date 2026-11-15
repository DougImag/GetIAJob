"""CV d'un utilisateur : liste, téléchargement, suppression et analyse en tâche de fond.

L'extraction elle-même est dans l'agent 1 (`agents.cv_analyzer`). Ici, on gère le statut de
l'analyse (pending → running → done / failed) pour qu'elle survive à un changement de page,
d'onglet ou de fenêtre : elle tourne dans un thread du serveur, pas dans la session du navigateur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import undefer

from getiajob.db import CVRecord, JobMatch, session_scope
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


def run_cv_analysis(cv_id: int, force: bool = True) -> None:
    """Exécuté dans un thread du serveur : analyse le CV et enregistre le statut."""
    from getiajob.agents.cv_analyzer import ensure_profile

    _set_status(cv_id, analysis_status="running", analysis_started_at=datetime.now(UTC))
    try:
        ensure_profile(cv_id, force=force)
        _set_status(cv_id, analysis_status="done", analysis_error=None)
    except Exception as exc:
        log.exception("Échec de l'analyse du CV %s", cv_id)
        _set_status(cv_id, analysis_status="failed", analysis_error=str(exc)[:2000])


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
