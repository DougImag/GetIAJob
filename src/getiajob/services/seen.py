"""Offres consultées : distingue sur les résultats les annonces déjà ouvertes par l'utilisateur."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from getiajob.db import JobView, session_scope


def seen_map(user_id: int) -> dict[int, datetime]:
    """job_id → date de la première consultation."""
    with session_scope() as session:
        rows = session.execute(select(JobView.job_id, JobView.viewed_at).where(JobView.user_id == user_id))
        return {job_id: viewed_at for job_id, viewed_at in rows}


def seen_ids(user_id: int) -> list[int]:
    """Offres déjà consultées, à exclure d'une nouvelle recherche (ordre stable)."""
    with session_scope() as session:
        return list(
            session.scalars(select(JobView.job_id).where(JobView.user_id == user_id).order_by(JobView.job_id))
        )


def mark_seen(user_id: int, job_id: int) -> None:
    """Enregistre la consultation (idempotent : la première date est conservée)."""
    with session_scope() as session:
        session.execute(insert(JobView).values(user_id=user_id, job_id=job_id).on_conflict_do_nothing())
