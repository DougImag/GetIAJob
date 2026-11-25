"""Arrêt d'une recherche en cours : le traitement vérifie régulièrement qu'il doit continuer.

`run_search` déclare la recherche courante (`search_context`) ; les agents appellent
`check_cancelled()` entre deux étapes coûteuses (offre collectée, évaluation par l'IA). Dès que la
recherche n'est plus « en cours » en base (arrêtée par l'utilisateur ou un administrateur),
`SearchCancelled` interrompt le traitement et libère la file.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import select

from getiajob.db import SearchSession, session_scope

_current: ContextVar[int | None] = ContextVar("getiajob_search_id", default=None)
_last_check: dict[int, float] = {}


class SearchCancelled(Exception):
    """La recherche a été arrêtée : le traitement s'interrompt sans modifier la recherche."""


@contextmanager
def search_context(search_id: int) -> Iterator[None]:
    token = _current.set(search_id)
    try:
        yield
    finally:
        _current.reset(token)
        _last_check.pop(search_id, None)


def check_cancelled(min_interval: float = 2.0) -> None:
    """Lève `SearchCancelled` si la recherche courante a été arrêtée (au plus une requête toutes les
    `min_interval` secondes ; sans effet hors d'une recherche, par exemple en ligne de commande)."""
    search_id = _current.get()
    if search_id is None:
        return
    now = time.monotonic()
    if now - _last_check.get(search_id, float("-inf")) < min_interval:
        return
    _last_check[search_id] = now
    with session_scope() as session:
        status = session.scalar(select(SearchSession.status).where(SearchSession.id == search_id))
    if status != "running":
        raise SearchCancelled(search_id)
