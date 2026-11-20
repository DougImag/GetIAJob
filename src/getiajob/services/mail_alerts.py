"""Alertes e-mail (APEC, Free-Work…) : relève de la boîte commune et rattachement des offres aux utilisateurs.

Chaque utilisateur a ses **types de mail** (un par site émetteur, cf. `MailType`). L'APEC est reconnue
d'office (format connu, extraction sans IA). Un mail d'un site inconnu crée un type « à valider » :
le mail reste dans la boîte ; une fois le type activé par l'utilisateur, ses offres (et celles des mails
déjà reçus de ce site) sont extraites par le LLM à la relève suivante.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select, text, update

from getiajob.agents.job_scraper import store_posting
from getiajob.config import get_settings
from getiajob.db import JobOffer, MailImport, MailType, User, get_engine, session_scope
from getiajob.scraping.apec_mail import Mailbox, ParsedAlert, parse_alert
from getiajob.scraping.apec_mail import _links as links
from getiajob.scraping.base import JobPosting
from getiajob.scraping.mail_offers import SOURCE as MAIL_SOURCE
from getiajob.scraping.mail_offers import extract_offers
from getiajob.services.monitoring import record

log = logging.getLogger(__name__)

STATUS_LABELS = {
    "imported": "Importé",
    "unknown_sender": "Expéditeur sans compte",
    "unverified": "Expéditeur non authentifié",
    "not_alert": "Aucune offre reconnue",
    "new_type": "Nouveau type de mail (à valider)",
    "ignored_type": "Type de mail ignoré",
}
TYPE_STATUS_LABELS = {"pending": "À valider", "enabled": "Actif", "ignored": "Ignoré"}
# Types reconnus d'office (extraction dédiée, sans IA)
BUILTIN_TYPES = {"apec.fr": "APEC"}
ALERT_SOURCES = ("apec", MAIL_SOURCE)
MIN_LINKS = 3  # en dessous, un mail n'est pas une alerte (simple message transféré)


@dataclass
class MailResult:
    subject: str
    sender: str | None
    received_at: datetime | None
    status: str
    user_label: str | None = None
    postings: list[JobPosting] = field(default_factory=list)
    mail_type: str | None = None
    type_label: str | None = None
    user_id: int | None = None


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


def type_label(domain: str, name: str | None) -> str:
    """Nom lisible d'un type de mail : nom affiché de l'émetteur, sinon le domaine (« free-work.com »)."""
    if domain in BUILTIN_TYPES:
        return BUILTIN_TYPES[domain]
    name = (name or "").strip()
    if name and "@" not in name and len(name) <= 60:
        return name
    return domain


def _mail_type(session, user_id: int, alert: ParsedAlert, create: bool) -> MailType | None:
    """Type de mail de l'utilisateur pour cette alerte ; créé à la première réception (« à valider »,
    sauf les types reconnus d'office)."""
    domain = alert.mail_type
    row = session.scalar(select(MailType).where(MailType.user_id == user_id, MailType.domain == domain))
    if row is None:
        row = MailType(
            user_id=user_id,
            domain=domain,
            label=type_label(domain, alert.origin_name),
            status="enabled" if domain in BUILTIN_TYPES else "pending",
            n_mails=0,
            n_offers=0,
        )
        if not create:
            return row  # aperçu : rien n'est enregistré
        session.add(row)
    if create:
        row.n_mails = (row.n_mails or 0) + 1
        row.last_seen_at = alert.received_at or datetime.now(UTC)
        row.sample_sender = (alert.origin or "")[:255] or row.sample_sender
        row.sample_subject = alert.subject or row.sample_subject
        session.flush()
    return row


def _classify(session, alert: ParsedAlert, user: User | None, dry_run: bool) -> tuple[str, MailType | None]:
    """Statut du mail et type de mail (s'il a pu être déterminé)."""
    if not alert.verified:
        return "unverified", None
    if user is None:
        return "unknown_sender", None
    if alert.mail_type is None:
        return "not_alert", None
    if alert.mail_type not in BUILTIN_TYPES and len(links(alert.message)) < MIN_LINKS:
        return "not_alert", None
    mt = _mail_type(session, user.id, alert, create=not dry_run)
    if mt.status == "pending":
        return "new_type", mt
    if mt.status == "ignored":
        return "ignored_type", mt
    return "imported", mt


def _postings(alert: ParsedAlert, mt: MailType, dry_run: bool) -> list[JobPosting]:
    if mt.domain in BUILTIN_TYPES:
        return alert.postings
    if dry_run:  # l'aperçu n'appelle pas l'IA
        return []
    return extract_offers(alert.message, mt.label, alert.received_at, alert.subject)


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
                    alert = parse_alert(raw, set(users))
                    user = next((users[a] for a in alert.candidates if a in users), None)
                    status, mt = _classify(session, alert, user, dry_run)
                    postings: list[JobPosting] = []
                    if status == "imported":
                        try:
                            postings = _postings(alert, mt, dry_run)
                        except Exception as exc:  # LLM indisponible : le mail sera relu à la prochaine relève
                            log.warning("Extraction des offres impossible (%s) : %s", alert.subject, exc)
                            session.rollback()
                            continue
                        if not postings and not (dry_run and mt.domain not in BUILTIN_TYPES):
                            status = "not_alert"
                    sender = user.email if user else (alert.candidates[0] if alert.candidates else None)
                    report.results.append(
                        MailResult(
                            alert.subject,
                            sender,
                            alert.received_at,
                            status,
                            user.full_name or user.email if user else None,
                            postings,
                            mt.domain if mt else alert.mail_type,
                            mt.label if mt else None,
                            user.id if user else None,
                        )
                    )
                    if dry_run:
                        continue
                    query = alert.alert_name or f"Alerte {mt.label}" if mt else "Alerte e-mail"
                    for posting in postings:
                        posting.source_id = f"{user.id}:{posting.source_id}"
                        existed = session.scalar(
                            select(JobOffer.id).where(JobOffer.external_id == posting.external_id)
                        )
                        store_posting(session, None, posting, query, user.id)
                        report.new_offers += existed is None
                    if mt is not None and postings:
                        mt.n_offers = (mt.n_offers or 0) + len(postings)
                    session.add(
                        MailImport(
                            message_key=key,
                            user_id=user.id if user else None,
                            sender=(sender or "")[:255] or None,
                            subject=alert.subject,
                            received_at=alert.received_at,
                            status=status,
                            mail_type=mt.domain if mt else alert.mail_type,
                            n_offers=len(postings),
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
    """Offres reçues par alerte e-mail (APEC, Free-Work…) par l'utilisateur sur la période."""
    since = datetime.now(UTC) - timedelta(days=days)
    with session_scope() as session:
        return list(
            session.scalars(
                select(JobOffer.id).where(
                    JobOffer.source.in_(ALERT_SOURCES),
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


# --------------------------------------------------------------------------- #
# Types de mail de l'utilisateur
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MailTypeRow:
    id: int
    domain: str
    label: str
    status: str
    n_mails: int
    n_offers: int
    sample_sender: str | None
    sample_subject: str | None
    last_seen_at: datetime | None

    @property
    def builtin(self) -> bool:
        return self.domain in BUILTIN_TYPES


def list_mail_types(user_id: int) -> list[MailTypeRow]:
    """Types de mail de l'utilisateur : à valider d'abord, puis actifs, puis ignorés."""
    order = {"pending": 0, "enabled": 1, "ignored": 2}
    with session_scope() as session:
        rows = session.scalars(select(MailType).where(MailType.user_id == user_id)).all()
        result = [
            MailTypeRow(
                r.id,
                r.domain,
                r.label,
                r.status,
                r.n_mails or 0,
                r.n_offers or 0,
                r.sample_sender,
                r.sample_subject,
                r.last_seen_at,
            )
            for r in rows
        ]
    return sorted(result, key=lambda r: (order.get(r.status, 9), r.label.lower()))


def pending_mail_types(user_id: int) -> int:
    with session_scope() as session:
        return session.scalar(
            select(func.count()).where(MailType.user_id == user_id, MailType.status == "pending")
        )


def set_mail_type_status(user_id: int, type_id: int, status: str) -> int:
    """Active, ignore ou remet « à valider » un type de mail.

    À l'activation, les mails déjà reçus de ce site (restés dans la boîte) sont oubliés pour être relus
    — et leurs offres importées — à la prochaine relève. Renvoie le nombre de mails concernés.
    """
    if status not in TYPE_STATUS_LABELS:
        raise ValueError(f"statut inconnu : {status}")
    with session_scope() as session:
        row = session.scalar(select(MailType).where(MailType.id == type_id, MailType.user_id == user_id))
        if row is None:
            return 0
        row.status = status
        if status != "enabled":
            return 0
        result = session.execute(
            delete(MailImport).where(
                MailImport.user_id == user_id,
                MailImport.mail_type == row.domain,
                MailImport.status.in_(("new_type", "ignored_type")),
            )
        )
        n = result.rowcount or 0
        row.n_mails = max(0, (row.n_mails or 0) - n)  # recomptés quand ils seront relus
        return n


def rename_mail_type(user_id: int, type_id: int, label: str) -> None:
    label = " ".join(label.split())[:100]
    if not label:
        return
    with session_scope() as session:
        row = session.scalar(select(MailType).where(MailType.id == type_id, MailType.user_id == user_id))
        if row is not None:
            row.label = label


# --------------------------------------------------------------------------- #
# Import lancé par l'utilisateur (page « Mes alertes e-mail »)
# --------------------------------------------------------------------------- #

IMPORT_COOLDOWN = timedelta(minutes=5)


class ImportThrottled(Exception):
    """Import manuel refusé : le précédent date de moins de 5 minutes."""

    def __init__(self, available_at: datetime):
        self.available_at = available_at
        wait = max(0, int((available_at - datetime.now(UTC)).total_seconds()))
        super().__init__(f"Prochain import possible dans {wait // 60} min {wait % 60:02d} s.")


def next_import_at(user_id: int, admin: bool = False) -> datetime | None:
    """Heure à partir de laquelle l'utilisateur peut relancer un import (None : tout de suite)."""
    if admin:
        return None
    with session_scope() as session:
        last = session.scalar(select(User.last_mail_import_at).where(User.id == user_id))
    if last is None or datetime.now(UTC) - last >= IMPORT_COOLDOWN:
        return None
    return last + IMPORT_COOLDOWN


def import_for_user(user_id: int, admin: bool = False) -> SyncReport:
    """Relève la boîte à la demande d'un utilisateur : au plus un import toutes les 5 minutes, sauf admin.

    La relève traite toute la boîte (les offres de chacun restent rattachées à leur expéditeur) ; le
    créneau est réservé de façon atomique, deux clics simultanés ne lancent qu'une relève.
    """
    now = datetime.now(UTC)
    with session_scope() as session:
        previous = session.scalar(select(User.last_mail_import_at).where(User.id == user_id))
        allowed = User.id == user_id
        if not admin:
            allowed = allowed & or_(
                User.last_mail_import_at.is_(None), User.last_mail_import_at <= now - IMPORT_COOLDOWN
            )
        reserved = session.execute(
            update(User).where(allowed).values(last_mail_import_at=now).returning(User.id)
        ).scalar()
    if reserved is None:
        raise ImportThrottled(next_import_at(user_id) or now)
    report = sync_mailbox()
    if report.skipped:  # une autre relève tournait : le créneau n'est pas consommé
        with session_scope() as session:
            session.execute(update(User).where(User.id == user_id).values(last_mail_import_at=previous))
    return report


@dataclass(frozen=True)
class UserMailRow:
    subject: str | None
    received_at: datetime | None
    status: str
    mail_type: str | None
    type_label: str | None
    n_offers: int


def user_imports(user_id: int, limit: int = 15) -> list[UserMailRow]:
    """Derniers mails de l'utilisateur traités par la relève."""
    with session_scope() as session:
        labels = dict(
            session.execute(select(MailType.domain, MailType.label).where(MailType.user_id == user_id)).all()
        )
        rows = session.scalars(
            select(MailImport)
            .where(MailImport.user_id == user_id)
            .order_by(MailImport.created_at.desc())
            .limit(limit)
        ).all()
        return [
            UserMailRow(
                m.subject,
                m.received_at or m.created_at,
                m.status,
                m.mail_type,
                labels.get(m.mail_type),
                m.n_offers or 0,
            )
            for m in rows
        ]
