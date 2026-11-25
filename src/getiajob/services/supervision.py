"""Supervision (pages Admin) : activité, recherches, stock d'offres, classement, IA, sources, santé.

Toutes les fonctions sont en lecture seule, sauf les actions d'administration explicitement
nommées (annuler / relancer une recherche, purger les offres) qui sont tracées dans le journal d'audit.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version

import httpx
from sqlalchemy import func, select, text, update

from getiajob.config import get_settings
from getiajob.db import (
    AuditLog,
    CVRecord,
    MetricEvent,
    MonitorAlert,
    SearchSession,
    User,
    get_engine,
    session_scope,
)
from getiajob.services.feedback import REASONS
from getiajob.services.monitoring import audit

STARTED_AT = datetime.now(UTC)  # démarrage du processus (≈ dernier déploiement du conteneur)
CANCELLED = "Arrêtée par un administrateur."


def _rows(sql: str, **params) -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def _since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def app_version() -> str:
    try:
        return version("getiajob")
    except PackageNotFoundError:
        return "dev"


# --------------------------------------------------------------------------- #
# Activité
# --------------------------------------------------------------------------- #


@dataclass
class Activity:
    searches: int
    failed: int
    avg_seconds: float | None
    active_7: int
    active_30: int
    users: int
    new_users: int
    cvs: int
    new_cvs: int
    per_day: list[dict] = field(default_factory=list)  # jour, statut, n
    cv_failures: list[dict] = field(default_factory=list)

    @property
    def failure_rate(self) -> float | None:
        return self.failed / self.searches if self.searches else None


def activity(days: int = 30) -> Activity:
    since = _since(days)
    stats = _rows(
        """
        SELECT count(*) AS n,
               count(*) FILTER (WHERE status = 'failed') AS failed,
               avg(extract(epoch FROM finished_at - started_at)) FILTER (WHERE status = 'done') AS avg_s
        FROM search_sessions WHERE created_at >= :since
        """,
        since=since,
    )[0]
    active = _rows(
        """
        SELECT count(DISTINCT id) FILTER (WHERE last_login_at >= :d7 OR id IN
                   (SELECT user_id FROM search_sessions WHERE created_at >= :d7)) AS a7,
               count(DISTINCT id) FILTER (WHERE last_login_at >= :d30 OR id IN
                   (SELECT user_id FROM search_sessions WHERE created_at >= :d30)) AS a30,
               count(*) AS users,
               count(*) FILTER (WHERE created_at >= :since) AS new_users
        FROM users
        """,
        d7=_since(7),
        d30=_since(30),
        since=since,
    )[0]
    cvs = _rows(
        "SELECT count(*) AS n, count(*) FILTER (WHERE created_at >= :since) AS new FROM cv_profiles",
        since=since,
    )[0]
    per_day = _rows(
        """
        SELECT date_trunc('day', created_at)::date AS jour,
               CASE status WHEN 'failed' THEN 'Échec' WHEN 'done' THEN 'Terminée'
                           WHEN 'cancelled' THEN 'Arrêtée' ELSE 'En cours' END AS statut,
               count(*) AS n
        FROM search_sessions WHERE created_at >= :since GROUP BY 1, 2 ORDER BY 1
        """,
        since=since,
    )
    cv_failures = _rows(
        """
        SELECT c.id, c.file_name, u.email, c.analysis_error, c.analysis_started_at
        FROM cv_profiles c LEFT JOIN users u ON u.id = c.user_id
        WHERE c.analysis_status = 'failed' ORDER BY c.analysis_started_at DESC NULLS LAST LIMIT 20
        """
    )
    return Activity(
        searches=stats["n"],
        failed=stats["failed"],
        avg_seconds=float(stats["avg_s"]) if stats["avg_s"] is not None else None,
        active_7=active["a7"],
        active_30=active["a30"],
        users=active["users"],
        new_users=active["new_users"],
        cvs=cvs["n"],
        new_cvs=cvs["new"],
        per_day=per_day,
        cv_failures=cv_failures,
    )


# --------------------------------------------------------------------------- #
# Recherches : file, blocages, échecs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SearchRow:
    id: int
    name: str
    email: str | None
    status: str
    step: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None

    @property
    def age_minutes(self) -> float:
        ref = self.started_at or self.created_at
        return (datetime.now(UTC) - ref).total_seconds() / 60

    def is_stuck(self, minutes: int) -> bool:
        return self.status == "running" and self.age_minutes > minutes


def _search_rows(where, limit: int, order) -> list[SearchRow]:
    with session_scope() as session:
        rows = session.execute(
            select(SearchSession, User.email)
            .join(User, User.id == SearchSession.user_id, isouter=True)
            .where(where)
            .order_by(order)
            .limit(limit)
        ).all()
        return [
            SearchRow(
                s.id, s.name, email, s.status, s.step, s.created_at, s.started_at, s.finished_at, s.error
            )
            for s, email in rows
        ]


def active_searches() -> list[SearchRow]:
    """Recherches en cours puis en attente, dans l'ordre de traitement."""
    return _search_rows(SearchSession.status.in_(("pending", "running")), 100, SearchSession.id)


def failed_searches(limit: int = 30, email: str | None = None) -> list[SearchRow]:
    where = SearchSession.status == "failed"
    if email:
        where = where & User.email.ilike(f"%{email.strip()}%")
    return _search_rows(where, limit, SearchSession.created_at.desc())


def cancel_search(actor_id: int, search_id: int) -> bool:
    """Marque en échec une recherche en attente ou bloquée.

    Une recherche en attente ne sera pas lancée. Une recherche en cours s'interrompt à la prochaine
    vérification (offre collectée ou évaluée) ; un appel déjà engagé (LLM, site d'annonces) se termine
    d'abord, sans modifier la recherche.
    """
    with session_scope() as session:
        result = session.execute(
            update(SearchSession)
            .where(SearchSession.id == search_id, SearchSession.status.in_(("pending", "running")))
            .values(status="failed", step=None, error=CANCELLED, finished_at=datetime.now(UTC))
        )
        done = bool(result.rowcount)
    if done:
        audit(actor_id, "search.cancel", f"recherche #{search_id}")
    return done


def retry_search(actor_id: int, search_id: int) -> bool:
    """Remet en file une recherche en échec (mêmes paramètres). Le lancement est fait par l'appelant."""
    with session_scope() as session:
        result = session.execute(
            update(SearchSession)
            .where(SearchSession.id == search_id, SearchSession.status == "failed")
            .values(
                status="pending",
                step=None,
                error=None,
                results=None,
                n_offers=None,
                started_at=None,
                finished_at=None,
            )
        )
        done = bool(result.rowcount)
    if done:
        audit(actor_id, "search.retry", f"recherche #{search_id}")
    return done


# --------------------------------------------------------------------------- #
# Stock d'offres
# --------------------------------------------------------------------------- #


@dataclass
class Stock:
    total: int
    public: int
    private: int
    no_description: int
    no_embedding: int
    no_company: int
    avg_age_days: float | None
    seen_7: int
    merged_30: int
    per_day: list[dict] = field(default_factory=list)  # jour, source, n
    by_source: list[dict] = field(default_factory=list)


def stock(days: int = 30) -> Stock:
    s = _rows(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE owner_user_id IS NULL) AS public,
               count(*) FILTER (WHERE owner_user_id IS NOT NULL) AS private,
               count(*) FILTER (WHERE description IS NULL OR description = '') AS no_desc,
               count(*) FILTER (WHERE embedding IS NULL) AS no_emb,
               count(*) FILTER (WHERE company IS NULL OR company = '') AS no_company,
               avg(extract(epoch FROM now() - coalesce(posted_at, first_seen_at)) / 86400) AS age,
               count(*) FILTER (WHERE last_seen_at >= :d7) AS seen_7
        FROM job_offers
        """,
        d7=_since(7),
    )[0]
    merged = _rows(
        "SELECT coalesce(sum(count), 0) AS n FROM metric_events WHERE kind = 'dedup' AND created_at >= :s",
        s=_since(30),
    )[0]["n"]
    per_day = _rows(
        """
        SELECT date_trunc('day', first_seen_at)::date AS jour, source, count(*) AS n
        FROM job_offers WHERE first_seen_at >= :since GROUP BY 1, 2 ORDER BY 1
        """,
        since=_since(days),
    )
    by_source = _rows(
        """
        SELECT source, count(*) AS n,
               count(*) FILTER (WHERE description IS NULL OR description = '') AS no_desc,
               count(*) FILTER (WHERE salary IS NOT NULL) AS with_salary,
               max(first_seen_at) AS last_new
        FROM job_offers GROUP BY source ORDER BY n DESC
        """
    )
    return Stock(
        total=s["total"],
        public=s["public"],
        private=s["private"],
        no_description=s["no_desc"],
        no_embedding=s["no_emb"],
        no_company=s["no_company"],
        avg_age_days=float(s["age"]) if s["age"] is not None else None,
        seen_7=s["seen_7"],
        merged_30=int(merged),
        per_day=per_day,
        by_source=by_source,
    )


# Une offre est conservée tant qu'elle a un avis, une candidature ou figure dans une recherche enregistrée
# (sinon les boutons 👍/👎 de cette recherche pointeraient vers une offre disparue).
_PURGEABLE = """
    FROM job_offers o
    WHERE o.last_seen_at < :before
      AND NOT EXISTS (SELECT 1 FROM job_feedback f WHERE f.job_id = o.id)
      AND NOT EXISTS (SELECT 1 FROM job_applications a WHERE a.job_id = o.id)
      AND NOT EXISTS (
          SELECT 1 FROM search_sessions s, jsonb_array_elements(coalesce(s.results, '[]'::jsonb)) r
          WHERE jsonb_typeof(s.results) = 'array' AND (r->>'job_id')::int = o.id
      )
"""


def purgeable_offers(days: int) -> int:
    return _rows(f"SELECT count(*) AS n {_PURGEABLE}", before=_since(days))[0]["n"]


def purge_offers(actor_id: int | None, days: int) -> int:
    """Supprime les offres non revues depuis `days` jours (sans avis, absentes des recherches)."""
    with get_engine().begin() as conn:
        n = conn.execute(
            text(f"DELETE FROM job_offers WHERE id IN (SELECT o.id {_PURGEABLE})"), {"before": _since(days)}
        ).rowcount
    audit(actor_id, "offers.purge", f"{n} offre(s)", f"non revues depuis {days} jours")
    return n or 0


# --------------------------------------------------------------------------- #
# Qualité du classement
# --------------------------------------------------------------------------- #


@dataclass
class Ranking:
    scores: list[float]
    likes: int
    dislikes: int
    by_source: list[dict] = field(default_factory=list)  # source, likes, dislikes
    reasons: list[dict] = field(default_factory=list)  # motif, n


def ranking(days: int = 30) -> Ranking:
    since = _since(days)
    scores = [
        float(r["s"])
        for r in _rows(
            "SELECT llm_score AS s FROM job_matches WHERE llm_score IS NOT NULL AND updated_at >= :since",
            since=since,
        )
    ]
    by_source = _rows(
        """
        SELECT o.source, count(*) FILTER (WHERE f.rating > 0) AS likes,
               count(*) FILTER (WHERE f.rating < 0) AS dislikes
        FROM job_feedback f JOIN job_offers o ON o.id = f.job_id
        WHERE f.updated_at >= :since GROUP BY o.source ORDER BY o.source
        """,
        since=since,
    )
    reasons = _rows(
        """
        SELECT r AS code, count(*) AS n
        FROM job_feedback f, jsonb_array_elements_text(coalesce(f.reasons, '[]'::jsonb)) r
        WHERE f.rating < 0 AND f.updated_at >= :since AND r <> 'seen'
        GROUP BY r ORDER BY n DESC LIMIT 10
        """,
        since=since,
    )
    return Ranking(
        scores=scores,
        likes=sum(r["likes"] for r in by_source),
        dislikes=sum(r["dislikes"] for r in by_source),
        by_source=by_source,
        reasons=[{"motif": REASONS.get(r["code"], r["code"]), "n": r["n"]} for r in reasons],
    )


# --------------------------------------------------------------------------- #
# Consommation de l'IA
# --------------------------------------------------------------------------- #

LLM_LABELS = {
    "evaluation": "Évaluation d'une offre",
    "plan_requetes": "Plan de requêtes",
    "cv_synthese": "CV : synthèse",
    "cv": "CV",
    "offres": "Offres",
}


def llm_label(name: str) -> str:
    if name in LLM_LABELS:
        return LLM_LABELS[name]
    return f"CV : {name.removeprefix('cv_')}" if name.startswith("cv_") else name


@dataclass
class AIUsage:
    steps: list[dict]  # kind, name, calls, errors, avg_ms, p95_ms, items
    per_day: list[dict]  # jour, appels
    evaluations: int
    cache_hits: int

    @property
    def cache_rate(self) -> float | None:
        total = self.evaluations + self.cache_hits
        return self.cache_hits / total if total else None


def ai_usage(days: int = 7) -> AIUsage:
    since = _since(days)
    steps = _rows(
        """
        SELECT kind, name, count(*) AS calls, count(*) FILTER (WHERE NOT ok) AS errors,
               avg(duration_ms) AS avg_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
               sum(count) AS items
        FROM metric_events WHERE kind IN ('llm', 'embedding') AND created_at >= :since
        GROUP BY kind, name ORDER BY calls DESC
        """,
        since=since,
    )
    per_day = _rows(
        """
        SELECT date_trunc('day', created_at)::date AS jour, count(*) AS appels
        FROM metric_events WHERE kind = 'llm' AND created_at >= :since GROUP BY 1 ORDER BY 1
        """,
        since=since,
    )
    cache = _rows(
        """
        SELECT coalesce(sum(count) FILTER (WHERE kind = 'cache'), 0) AS hits,
               count(*) FILTER (WHERE kind = 'llm' AND name = 'evaluation') AS evals
        FROM metric_events WHERE created_at >= :since
        """,
        since=since,
    )[0]
    return AIUsage(steps, per_day, int(cache["evals"]), int(cache["hits"]))


# --------------------------------------------------------------------------- #
# Santé des sources
# --------------------------------------------------------------------------- #


@dataclass
class SourceHealth:
    name: str
    requests: int = 0
    errors: int = 0
    throttled: int = 0  # HTTP 429 (trop de requêtes : blocage probable)
    avg_ms: float | None = None
    today: int = 0
    queries: int = 0
    failed_queries: int = 0
    empty_queries: int = 0
    consecutive_failures: int = 0
    last_ok: datetime | None = None
    last_error: str | None = None


def source_health(days: int = 7) -> dict[str, SourceHealth]:
    since = _since(days)
    out: dict[str, SourceHealth] = {}
    for r in _rows(
        """
        SELECT name, count(*) AS n, count(*) FILTER (WHERE NOT ok) AS errors,
               count(*) FILTER (WHERE detail = '429') AS throttled, avg(duration_ms) AS avg_ms,
               count(*) FILTER (WHERE created_at >= date_trunc('day', now())) AS today
        FROM metric_events WHERE kind = 'http' AND created_at >= :since GROUP BY name
        """,
        since=since,
    ):
        out[r["name"]] = SourceHealth(
            r["name"],
            r["n"],
            r["errors"],
            r["throttled"],
            float(r["avg_ms"]) if r["avg_ms"] is not None else None,
            r["today"],
        )
    for r in _rows(
        """
        SELECT name, count(*) AS n, count(*) FILTER (WHERE NOT ok) AS failed,
               count(*) FILTER (WHERE ok AND count = 0) AS empty,
               max(created_at) FILTER (WHERE ok) AS last_ok
        FROM metric_events WHERE kind = 'source' AND created_at >= :since GROUP BY name
        """,
        since=since,
    ):
        h = out.setdefault(r["name"], SourceHealth(r["name"]))
        h.queries, h.failed_queries, h.empty_queries, h.last_ok = (
            r["n"],
            r["failed"],
            r["empty"],
            r["last_ok"],
        )
    with session_scope() as session:
        for name, h in out.items():
            recent = session.execute(
                select(MetricEvent.ok, MetricEvent.detail)
                .where(MetricEvent.kind == "source", MetricEvent.name == name)
                .order_by(MetricEvent.created_at.desc())
                .limit(20)
            ).all()
            for ok, detail in recent:
                if ok:
                    break
                h.consecutive_failures += 1
                h.last_error = h.last_error or detail
    return out


# --------------------------------------------------------------------------- #
# Santé du système
# --------------------------------------------------------------------------- #


@dataclass
class Check:
    label: str
    ok: bool | None  # None = non applicable / inconnu
    detail: str
    seconds: float | None = None


def check_database() -> tuple[Check, list[dict]]:
    start = time.monotonic()
    try:
        size = _rows("SELECT pg_size_pretty(pg_database_size(current_database())) AS s")[0]["s"]
        tables = _rows(
            """
            SELECT relname AS "table", n_live_tup AS lignes,
                   pg_size_pretty(pg_total_relation_size(relid)) AS taille,
                   pg_total_relation_size(relid) AS octets
            FROM pg_stat_user_tables ORDER BY octets DESC LIMIT 8
            """
        )
    except Exception as exc:
        return Check("PostgreSQL", False, f"{type(exc).__name__} : {exc}"), []
    return Check("PostgreSQL", True, f"Base : {size}", time.monotonic() - start), tables


def check_ollama() -> Check:
    s = get_settings()
    start = time.monotonic()
    try:
        with httpx.Client(base_url=s.ollama_base_url, timeout=5) as client:
            tags = {m["name"] for m in client.get("/api/tags").raise_for_status().json().get("models", [])}
            loaded = [m["name"] for m in client.get("/api/ps").raise_for_status().json().get("models", [])]
    except Exception as exc:
        return Check("Ollama", False, f"Injoignable ({type(exc).__name__})")
    # Avec un LLM distant (LLM_PROVIDER=openai), Ollama ne sert plus qu'aux embeddings
    needed = (s.llm_model, s.embedding_model) if s.llm_provider == "ollama" else (s.embedding_model,)
    missing = [m for m in needed if m not in tags and f"{m}:latest" not in tags]
    detail = f"{len(tags)} modèle(s) installé(s) · en mémoire : {', '.join(loaded) or 'aucun'}"
    if missing:
        return Check("Ollama", False, f"Modèle(s) manquant(s) : {', '.join(missing)} · {detail}")
    return Check("Ollama", True, detail, time.monotonic() - start)


def check_remote_llm() -> Check | None:
    """API compatible OpenAI (LLM_PROVIDER=openai) : joignable et modèle proposé. None si LLM sur Ollama."""
    s = get_settings()
    if s.llm_provider != "openai":
        return None
    start = time.monotonic()
    headers = {"Authorization": f"Bearer {s.llm_api_key}"} if s.llm_api_key else {}
    try:
        with httpx.Client(timeout=10, headers=headers) as client:
            data = client.get(f"{s.llm_base_url.rstrip('/')}/models").raise_for_status().json()
    except httpx.HTTPStatusError as exc:
        hint = " (clé API refusée)" if exc.response.status_code in (401, 403) else ""
        body = exc.response.text[:150]
        return Check("LLM distant", False, f"HTTP {exc.response.status_code}{hint} : {body}")
    except Exception as exc:
        return Check("LLM distant", False, f"Injoignable ({type(exc).__name__}) · {s.llm_base_url}")
    ids = {m.get("id") for m in data.get("data", [])}
    access = "clé API" if s.llm_api_key else "sans clé API (accès anonyme très limité)"
    if ids and s.llm_model not in ids:
        return Check("LLM distant", False, f"Modèle {s.llm_model} non proposé par {s.llm_base_url}")
    return Check("LLM distant", True, f"{s.llm_model} · {access}", time.monotonic() - start)


def check_mailbox() -> Check:
    """Connexion IMAP réelle (sur demande : quelques secondes)."""
    from getiajob.services.mail_alerts import _mailbox, mailbox_configured

    if not mailbox_configured():
        return Check("Boîte des alertes", None, "Non configurée")
    start = time.monotonic()
    try:
        with _mailbox(readonly=True):
            pass
    except Exception as exc:
        return Check("Boîte des alertes", False, f"{type(exc).__name__} : {exc}")
    return Check("Boîte des alertes", True, "Connexion IMAP réussie", time.monotonic() - start)


def last_event(kind: str, ok: bool | None = True) -> MetricEvent | None:
    with session_scope() as session:
        query = select(MetricEvent).where(MetricEvent.kind == kind)
        if ok is not None:
            query = query.where(MetricEvent.ok.is_(ok))
        return session.scalar(query.order_by(MetricEvent.created_at.desc()).limit(1))


# --------------------------------------------------------------------------- #
# Journal d'audit et alertes
# --------------------------------------------------------------------------- #

AUDIT_LABELS = {
    "user.admin_grant": "Rôle administrateur donné",
    "user.admin_revoke": "Rôle administrateur retiré",
    "user.delete": "Compte supprimé",
    "user.impersonate": "Consultation d'un compte (lecture seule)",
    "user.impersonate_end": "Fin de consultation d'un compte",
    "user.impersonate_write": "Consultation : mode édition activé",
    "user.impersonate_readonly": "Consultation : retour en lecture seule",
    "search.cancel": "Recherche arrêtée",
    "search.retry": "Recherche relancée",
    "offers.purge": "Offres purgées",
    "mail.import": "Relève des alertes e-mail",
    "metrics.prune": "Mesures anciennes supprimées",
    "backup": "Sauvegarde de la base",
    "alert.notify": "Alerte envoyée",
}


def audit_log(limit: int = 200, action: str | None = None) -> list[AuditLog]:
    with session_scope() as session:
        query = select(AuditLog)
        if action:
            query = query.where(AuditLog.action == action)
        return list(session.scalars(query.order_by(AuditLog.created_at.desc()).limit(limit)))


def audit_actions() -> list[str]:
    with session_scope() as session:
        return sorted(session.scalars(select(AuditLog.action).distinct()))


def alerts(include_resolved_days: int = 7) -> list[MonitorAlert]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(MonitorAlert)
                .where(
                    MonitorAlert.resolved_at.is_(None)
                    | (MonitorAlert.resolved_at >= _since(include_resolved_days))
                )
                .order_by(MonitorAlert.resolved_at.is_not(None), MonitorAlert.opened_at.desc())
            )
        )


def cv_failure_count() -> int:
    with session_scope() as session:
        return session.scalar(select(func.count()).where(CVRecord.analysis_status == "failed")) or 0
