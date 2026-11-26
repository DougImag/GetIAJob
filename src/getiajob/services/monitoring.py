"""Mesures techniques et journal d'audit.

L'enregistrement ne doit jamais faire échouer le traitement mesuré : toute erreur est seulement
journalisée. Chaque mesure est écrite dans sa propre transaction.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select

from getiajob.db import AuditLog, MetricEvent, User, session_scope

log = logging.getLogger(__name__)

SYSTEM = "système"


def record(
    kind: str,
    name: str,
    *,
    ok: bool = True,
    duration_ms: int | None = None,
    count: int = 1,
    detail: str | None = None,
) -> None:
    try:
        with session_scope() as session:
            session.add(
                MetricEvent(
                    kind=kind,
                    name=name[:60],
                    ok=ok,
                    duration_ms=duration_ms,
                    count=count,
                    detail=(detail or None) and detail[:2000],
                )
            )
    except Exception as exc:  # la supervision ne bloque jamais le métier
        log.debug("Mesure %s/%s non enregistrée : %s", kind, name, exc)


@contextmanager
def timed(kind: str, name: str, count: int = 1) -> Iterator[None]:
    """Mesure la durée du bloc ; une exception est enregistrée comme échec puis relancée.

    Pour un appel au LLM (`kind="llm"`), `name` sert aussi d'étape au suivi des tokens (`usage`).
    """
    from getiajob.services import usage

    start = time.monotonic()
    try:
        if kind == "llm":
            with usage.step(name):
                yield
        else:
            yield
    except Exception as exc:
        record(
            kind,
            name,
            ok=False,
            duration_ms=int((time.monotonic() - start) * 1000),
            count=count,
            detail=f"{type(exc).__name__} : {exc}",
        )
        raise
    record(kind, name, duration_ms=int((time.monotonic() - start) * 1000), count=count)


def http_hooks(source: str) -> dict:
    """Hooks httpx : chaque réponse d'une source est comptée (code HTTP, durée)."""

    def on_request(request: httpx.Request) -> None:
        request.extensions["gj_start"] = time.monotonic()

    def on_response(response: httpx.Response) -> None:
        start = response.request.extensions.get("gj_start")
        ms = int((time.monotonic() - start) * 1000) if start else None
        code = response.status_code
        record("http", source, ok=code < 400, duration_ms=ms, detail=str(code))

    return {"request": [on_request], "response": [on_response]}


def audit(actor_id: int | None, action: str, target: str | None = None, detail: str | None = None) -> None:
    try:
        with session_scope() as session:
            email = session.scalar(select(User.email).where(User.id == actor_id)) if actor_id else None
            session.add(
                AuditLog(
                    actor_id=actor_id,
                    actor=email or SYSTEM,
                    action=action[:60],
                    target=(target or None) and target[:255],
                    detail=detail,
                )
            )
    except Exception as exc:
        log.warning("Audit « %s » non enregistré : %s", action, exc)


def prune_metrics(days: int) -> int:
    """Supprime les mesures plus anciennes que `days` jours (le journal d'audit est conservé)."""
    with session_scope() as session:
        result = session.execute(
            delete(MetricEvent).where(MetricEvent.created_at < datetime.now(UTC) - timedelta(days=days))
        )
        return result.rowcount or 0
