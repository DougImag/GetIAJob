"""Exécution en tâche de fond, dans le processus serveur (indépendante des onglets du navigateur).

Deux files d'un seul worker chacune : les recherches et les analyses de CV. Une analyse de CV
n'attend donc pas la fin d'une recherche longue (Ollama sérialise de toute façon les appels).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, update

from getiajob.db import CVRecord, SearchSession, session_scope
from getiajob.services.cvs import request_analysis, run_cv_analysis
from getiajob.services.searches import run_search

log = logging.getLogger(__name__)

INTERRUPTED = "Interrompue par un redémarrage du serveur."


class BackgroundRunner:
    def __init__(self) -> None:
        self._searches = ThreadPoolExecutor(max_workers=1, thread_name_prefix="search")
        self._cvs = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cv")
        self._recover()

    def submit_search(self, search_id: int) -> None:
        self._searches.submit(run_search, search_id)

    def submit_cv_analysis(self, cv_id: int, force: bool = True) -> bool:
        """Met l'analyse en file. False si elle est déjà en attente ou en cours."""
        if not request_analysis(cv_id):
            return False
        self._cvs.submit(run_cv_analysis, cv_id, force)
        return True

    def _recover(self) -> None:
        """Au démarrage : ce qui était « en cours » a été interrompu, ce qui était « en attente » repart."""
        with session_scope() as session:
            session.execute(
                update(SearchSession)
                .where(SearchSession.status == "running")
                .values(status="failed", error=INTERRUPTED, step=None)
            )
            pending_searches = session.scalars(
                select(SearchSession.id).where(SearchSession.status == "pending").order_by(SearchSession.id)
            ).all()
            # Une analyse de CV interrompue est simplement relancée (aucun effet de bord partiel).
            session.execute(
                update(CVRecord)
                .where(CVRecord.analysis_status == "running")
                .values(analysis_status="pending")
            )
            pending_cvs = session.scalars(
                select(CVRecord.id).where(CVRecord.analysis_status == "pending").order_by(CVRecord.id)
            ).all()
        for search_id in pending_searches:
            self.submit_search(search_id)
        for cv_id in pending_cvs:
            self._cvs.submit(run_cv_analysis, cv_id, True)
        if pending_searches or pending_cvs:
            log.info(
                "Reprise : %d recherche(s), %d analyse(s) de CV.", len(pending_searches), len(pending_cvs)
            )
