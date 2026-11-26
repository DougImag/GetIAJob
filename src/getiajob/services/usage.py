"""Consommation de tokens du LLM : enregistrement de chaque appel, rattaché à un utilisateur.

L'utilisateur et l'étape sont portés par des variables de contexte, posées par l'appelant :
- `for_user(user_id)` autour d'une analyse de CV, d'une recherche ou du traitement d'un mail ;
- l'étape est posée par `monitoring.timed("llm", …)`, qui entoure déjà chaque appel au LLM.
Comme pour la supervision, un échec d'enregistrement ne fait jamais échouer le traitement.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from getiajob.db import LLMUsage, session_scope

log = logging.getLogger(__name__)

_user: ContextVar[int | None] = ContextVar("getiajob_usage_user", default=None)
_step: ContextVar[str | None] = ContextVar("getiajob_usage_step", default=None)


@contextmanager
def for_user(user_id: int | None) -> Iterator[None]:
    """Les appels au LLM faits dans ce bloc sont comptés pour `user_id`."""
    token = _user.set(user_id)
    try:
        yield
    finally:
        _user.reset(token)


def current_user() -> int | None:
    """Utilisateur pour qui les appels au LLM sont faits (posé par `for_user`)."""
    return _user.get()


@contextmanager
def step(name: str) -> Iterator[None]:
    token = _step.set(name)
    try:
        yield
    finally:
        _step.reset(token)


def tokens(message: Any) -> tuple[int, int, int] | None:
    """(envoyés, générés, dont raisonnement) d'une réponse LangChain ; None si non renseigné."""
    usage = getattr(message, "usage_metadata", None)
    if not usage:
        return None
    details = usage.get("output_token_details") or {}
    return (
        int(usage.get("input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        int(details.get("reasoning") or 0),
    )


def cost(input_tokens: int, output_tokens: int) -> float | None:
    """Coût estimé avec les prix du LLM actif (par million de tokens) ; None si aucun prix n'est saisi."""
    from getiajob.services.llm_settings import current

    p = current()
    if p.price_input is None and p.price_output is None:
        return None
    return (input_tokens * (p.price_input or 0) + output_tokens * (p.price_output or 0)) / 1e6


def month_tokens(user_id: int) -> int:
    """Tokens (envoyés + générés) consommés par l'utilisateur depuis le 1er du mois (fuseau d'affichage)."""
    from sqlalchemy import func, select

    from getiajob.services.llm_settings import period_start

    try:
        with session_scope() as session:
            value = session.scalar(
                select(func.coalesce(func.sum(LLMUsage.input_tokens + LLMUsage.output_tokens), 0)).where(
                    LLMUsage.user_id == user_id, LLMUsage.created_at >= period_start("month")
                )
            )
    except Exception as exc:  # affichage seulement : jamais bloquant
        log.debug("Consommation du mois illisible : %s", exc)
        return 0
    return int(value or 0)


def record_message(message: Any, input_chars: int | None = None) -> None:
    """Enregistre les tokens d'une réponse du LLM (sans effet si le fournisseur ne les donne pas)."""
    counts = tokens(message)
    if counts is None:
        return
    from getiajob.services.llm_settings import current

    profile = current()
    n_in, n_out, n_reasoning = counts
    try:
        with session_scope() as session:
            session.add(
                LLMUsage(
                    user_id=_user.get(),
                    step=(_step.get() or "autre")[:60],
                    provider=profile.provider,
                    model=profile.model[:120],
                    input_tokens=n_in,
                    output_tokens=n_out,
                    reasoning_tokens=n_reasoning,
                    input_chars=input_chars,
                    cost=cost(n_in, n_out),
                )
            )
    except Exception as exc:  # le suivi ne bloque jamais le métier
        log.debug("Consommation LLM non enregistrée : %s", exc)
