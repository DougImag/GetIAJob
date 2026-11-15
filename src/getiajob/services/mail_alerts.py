"""Alertes e-mail (APEC) : relève de la boîte commune et rattachement des offres aux utilisateurs."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, text

from getiajob.agents.job_scraper import store_posting
from getiajob.config import get_settings
from getiajob.db import JobOffer, MailImport, User, get_engine, session_scope
from getiajob.scraping.apec_mail import Mailbox, ParsedAlert, parse_alert
from getiajob.scraping.base import JobPosting
from getiajob.services.monitoring import record

log = logging.getLogger(__name__)

STATUS_LABELS = {
    "imported": "Importé",
    "unknown_sender": "Expéditeur sans compte",
    "unverified": "Expéditeur non authentifié",
    "not_alert": "Pas une alerte APEC",
}


@dataclass
class MailResult:
    subject: str
    sender: str | None
    received_at: datetime | None
    status: str
    user_label: str | None = None
    postings: list[JobPosting] = field(default_factory=list)


@dataclass
class SyncReport:
    results: list[MailResult] = field(default_factory=list)
    new_offers: int = 0
    deleted: int = 0
    skipped: bool = False  # une autre relève était déjà en cours
    error: str | None = None
    seconds: float = 0.0

    @property
    def imported(self) -> int:
        return sum(1 for r in self.results if r.status == "imported")


def mailbox_configured() -> bool:
    s = get_settings()
    return bool(s.imap_host and s.imap_user and s.imap_password)


def _mailbox(readonly: bool = True) -> Mailbox:
    s = get_settings()
    return Mailbox(s.imap_host, s.imap_user, s.imap_password, s.imap_port, s.imap_folder, readonly)


def _status(alert: ParsedAlert, user: User | None) -> str:
    if not alert.is_alert:
        return "not_alert"
    if not alert.verified:
        return "unverified"
    if user is None:
        return "unknown_sender"
    return "imported"


LOCK_KEY = 0x6A0B_A1E5  # verrou consultatif PostgreSQL propre à la relève des alertes


def _try_lock():
    """Verrou PostgreSQL : une seule relève à la fois (cron, recherche web, bouton admin)."""
    conn = get_engine().connect()
    if conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar():
        return conn
    conn.close()
    return None


def _unlock(conn) -> None:
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    finally:
        conn.close()


def sync_mailbox(*, dry_run: bool = False) -> SyncReport:
    """Relève les nouveaux mails ; importe les offres des alertes envoyées par un utilisateur connu.

    `dry_run` : analyse sans rien enregistrer ni supprimer (y compris les mails déjà traités).
    Sinon, si MAIL_DELETE_PROCESSED est actif, les alertes importées sont supprimées de la boîte
    (déplacées dans sa corbeille) une fois enregistrées en base ; les autres mails sont conservés.
    """
    import time

    start = time.monotonic()
    report = SyncReport()
    if not mailbox_configured():
        report.error = "Boîte non configurée : renseigner IMAP_HOST, IMAP_USER et IMAP_PASSWORD dans .env."
        return report
    lock = None if dry_run else _try_lock()
    if not dry_run and lock is None:
        report.skipped = True
        report.seconds = time.monotonic() - start
        log.info("Relève des alertes déjà en cours ailleurs : ignorée.")
        return report
    try:
        with session_scope() as session:
            settings = get_settings()
            delete = settings.mail_delete_processed and not dry_run
            statuses = (
                {}
                if dry_run
                else dict(session.execute(select(MailImport.message_key, MailImport.status)).all())
            )
            known = set(statuses)
            users = {u.email.lower(): u for u in session.scalars(select(User))}
            to_delete: list[bytes] = []
            with _mailbox(readonly=not delete) as box:
                for uid, key, raw in box.messages(settings.mail_lookback_days, known):
                    if key in known or raw is None:
                        # Déjà traité (ex. import antérieur à la suppression automatique)
                        if delete and statuses.get(key) == "imported":
                            to_delete.append(uid)
                        continue
                    alert = parse_alert(raw)
                    user = users.get(alert.sender or "")
                    status = _status(alert, user)
                    report.results.append(
                        MailResult(
                            alert.subject,
                            alert.sender,
                            alert.received_at,
                            status,
                            user.full_name or user.email if user else None,
                            alert.postings,
                        )
                    )
                    if dry_run:
                        continue
                    if status == "imported":
                        for posting in alert.postings:
                            posting.source_id = f"{user.id}:{posting.source_id}"
                            existed = session.scalar(
                                select(JobOffer.id).where(JobOffer.external_id == posting.external_id)
                            )
                            store_posting(session, None, posting, alert.alert_name or "Alerte APEC", user.id)
                            report.new_offers += existed is None
                    session.add(
                        MailImport(
                            message_key=key,
                            user_id=user.id if user and status == "imported" else None,
                            sender=(alert.sender or "")[:255] or None,
                            subject=alert.subject,
                            received_at=alert.received_at,
                            status=status,
                            n_offers=len(alert.postings) if status == "imported" else 0,
                        )
                    )
                    known.add(key)
                    session.commit()  # enregistré en base avant toute suppression dans la boîte
                    if delete and status == "imported":
                        to_delete.append(uid)
                if to_delete:
                    report.deleted = box.delete(to_delete)
    except Exception as exc:
        log.warning("Relève des alertes e-mail impossible : %s", exc)
        report.error = f"{type(exc).__name__} : {exc}"
    finally:
        if lock is not None:
            _unlock(lock)
    report.seconds = time.monotonic() - start
    if not dry_run:
        record(
            "mail_sync",
            "imap",
            ok=report.error is None,
            duration_ms=int(report.seconds * 1000),
            count=report.new_offers,
            detail=report.error,
        )
    if report.results:
        log.info(
            "Alertes e-mail : %d mail(s), %d importé(s), %d nouvelle(s) offre(s), %d supprimé(s).",
            len(report.results),
            report.imported,
            report.new_offers,
            report.deleted,
        )
    return report


def user_alert_job_ids(user_id: int, days: int) -> list[int]:
    """Offres APEC de l'utilisateur reçues (ou publiées) sur la période."""
    since = datetime.now(UTC) - timedelta(days=days)
    with session_scope() as session:
        return list(
            session.scalars(
                select(JobOffer.id).where(
                    JobOffer.source == "apec",
                    JobOffer.owner_user_id == user_id,
                    or_(JobOffer.posted_at >= since, JobOffer.first_seen_at >= since),
                )
            )
        )


@dataclass(frozen=True)
class ImportRow:
    subject: str | None
    sender: str | None
    user_label: str | None
    received_at: datetime | None
    status: str
    n_offers: int


def recent_imports(limit: int = 20) -> list[ImportRow]:
    with session_scope() as session:
        rows = session.execute(
            select(MailImport, User)
            .outerjoin(User, MailImport.user_id == User.id)
            .order_by(MailImport.created_at.desc())
            .limit(limit)
        ).all()
        return [
            ImportRow(
                m.subject,
                m.sender,
                (u.full_name or u.email) if u else None,
                m.received_at or m.created_at,
                m.status,
                m.n_offers,
            )
            for m, u in rows
        ]


def import_counts() -> dict[str, int]:
    with session_scope() as session:
        return dict(
            session.execute(select(MailImport.status, func.count()).group_by(MailImport.status)).all()
        )
