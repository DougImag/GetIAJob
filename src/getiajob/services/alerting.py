"""Surveillance automatique (`getiajob monitor`, lancé par cron) et notifications e-mail.

Chaque problème détecté est une alerte à clé stable (ex. `source.linkedin.failing`) :
- nouvelle → enregistrée et notifiée aux administrateurs ;
- toujours présente → rappel toutes les ALERT_REMIND_HOURS heures ;
- disparue → marquée résolue (et signalée dans la notification suivante).
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import func, select

from getiajob.config import get_settings
from getiajob.db import CVRecord, MailImport, MetricEvent, MonitorAlert, SearchSession, User, session_scope
from getiajob.scraping.base import SOURCE_LABELS
from getiajob.services import supervision
from getiajob.services.monitoring import audit, prune_metrics

log = logging.getLogger(__name__)

SEVERITY_LABELS = {"critical": "Critique", "warning": "Attention"}


@dataclass(frozen=True)
class Finding:
    key: str
    severity: str  # warning, critical
    message: str


# --------------------------------------------------------------------------- #
# Détection
# --------------------------------------------------------------------------- #


def _ago(minutes: float) -> str:
    if minutes < 120:
        return f"{minutes:.0f} min"
    if minutes < 48 * 60:
        return f"{minutes / 60:.0f} h"
    return f"{minutes / 1440:.0f} j"


def _minutes_since(when: datetime | None, now: datetime) -> float | None:
    return None if when is None else (now - when).total_seconds() / 60


def detect(now: datetime | None = None) -> list[Finding]:
    """Passe en revue l'état de l'application et renvoie les problèmes constatés."""
    from getiajob.services.mail_alerts import mailbox_configured

    s = get_settings()
    now = now or datetime.now(UTC)
    found: list[Finding] = []

    stuck = [r for r in supervision.active_searches() if r.is_stuck(s.stuck_search_minutes)]
    if stuck:
        names = ", ".join(f"#{r.id} ({_ago(r.age_minutes)})" for r in stuck[:5])
        found.append(
            Finding("search.stuck", "warning", f"Recherche(s) en cours depuis trop longtemps : {names}.")
        )

    with session_scope() as session:
        day = now - timedelta(hours=24)
        total, failed = session.execute(
            select(func.count(), func.count().filter(SearchSession.status == "failed")).where(
                SearchSession.created_at >= day, SearchSession.status.in_(("done", "failed"))
            )
        ).one()
        if total >= 4 and failed / total >= s.search_failure_rate:
            found.append(
                Finding(
                    "search.failures",
                    "critical",
                    f"{failed} recherche(s) en échec sur {total} ces dernières 24 h.",
                )
            )
        cv_failed = session.scalar(
            select(func.count()).where(
                CVRecord.analysis_status == "failed", CVRecord.analysis_started_at >= day
            )
        )
        if cv_failed:
            found.append(Finding("cv.failures", "warning", f"{cv_failed} analyse(s) de CV en échec en 24 h."))

        if mailbox_configured():
            last_any = session.scalar(
                select(MetricEvent)
                .where(MetricEvent.kind == "mail_sync")
                .order_by(MetricEvent.id.desc())
                .limit(1)
            )
            last_ok = session.scalar(
                select(func.max(MetricEvent.created_at)).where(
                    MetricEvent.kind == "mail_sync", MetricEvent.ok.is_(True)
                )
            )
            # Seulement si la relève a déjà tourné (sinon : pas de tâche planifiée, rien à surveiller)
            if last_any is not None:
                late = _minutes_since(last_ok, now)
                if late is None or late > s.mail_stale_minutes:
                    reason = (
                        f" Dernière erreur : {last_any.detail}" if not last_any.ok and last_any.detail else ""
                    )
                    since = f"depuis {_ago(late)}" if late is not None else "jamais réussie"
                    found.append(
                        Finding("mail.stale", "warning", f"Relève des alertes e-mail {since}.{reason}")
                    )
            pending = session.scalar(
                select(func.count()).where(
                    MailImport.status.in_(("unknown_sender", "unverified")),
                    MailImport.created_at >= now - timedelta(days=7),
                )
            )
            if pending:
                found.append(
                    Finding(
                        "mail.unassigned",
                        "warning",
                        f"{pending} mail(s) d'alerte non rattaché(s) à un compte (7 derniers jours) : "
                        "expéditeur inconnu ou non authentifié.",
                    )
                )

        last_backup = session.scalar(
            select(func.max(MetricEvent.created_at)).where(
                MetricEvent.kind == "backup", MetricEvent.ok.is_(True)
            )
        )
        if last_backup is not None:
            late = _minutes_since(last_backup, now) or 0
            if late > s.backup_stale_hours * 60:
                found.append(
                    Finding("backup.stale", "warning", f"Dernière sauvegarde de la base il y a {_ago(late)}.")
                )

    for name, h in supervision.source_health(days=2).items():
        label = SOURCE_LABELS.get(name, name)
        if h.consecutive_failures >= s.source_failure_threshold:
            found.append(
                Finding(
                    f"source.{name}.failing",
                    "critical",
                    f"{label} : {h.consecutive_failures} échecs consécutifs. {h.last_error or ''}".strip(),
                )
            )
        if h.throttled >= 3:
            found.append(
                Finding(
                    f"source.{name}.throttled",
                    "warning",
                    f"{label} : {h.throttled} réponse(s) HTTP 429 (trop de requêtes) en 48 h, "
                    "blocage probable.",
                )
            )
        if name == "adzuna" and s.adzuna_daily_quota and h.today >= 0.9 * s.adzuna_daily_quota:
            found.append(
                Finding(
                    "source.adzuna.quota",
                    "warning",
                    f"Adzuna : {h.today} requêtes aujourd'hui (quota indicatif {s.adzuna_daily_quota}).",
                )
            )

    ollama = supervision.check_ollama()
    if not ollama.ok:
        found.append(Finding("ollama.down", "critical", f"Ollama : {ollama.detail}"))
    remote = supervision.check_remote_llm()
    if remote is not None and not remote.ok:
        found.append(Finding("llm.down", "critical", f"LLM distant : {remote.detail}"))
    return found


# --------------------------------------------------------------------------- #
# Cycle de vie des alertes
# --------------------------------------------------------------------------- #


@dataclass
class Plan:
    opened: list[Finding] = field(default_factory=list)
    reminded: list[Finding] = field(default_factory=list)
    ongoing: list[Finding] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)  # clés

    @property
    def to_notify(self) -> bool:
        return bool(self.opened or self.reminded or self.resolved)


def plan(
    findings: list[Finding],
    open_alerts: dict[str, datetime | None],
    now: datetime,
    remind_hours: int,
    retry_unnotified: bool = True,
) -> Plan:
    """Compare les constats aux alertes ouvertes (clé → date de dernière notification).

    `retry_unnotified` : une alerte jamais notifiée (envoi précédent en échec) est renvoyée ;
    à désactiver quand les notifications sont coupées.
    """
    result = Plan()
    current = {f.key: f for f in findings}
    for key, f in current.items():
        if key not in open_alerts:
            result.opened.append(f)
        elif (notified := open_alerts[key]) is None:
            (result.reminded if retry_unnotified else result.ongoing).append(f)
        elif now - notified >= timedelta(hours=remind_hours):
            result.reminded.append(f)
        else:
            result.ongoing.append(f)
    result.resolved = [k for k in open_alerts if k not in current]
    return result


@dataclass
class MonitorReport:
    findings: list[Finding]
    plan: Plan
    resolved_messages: dict[str, str] = field(default_factory=dict)
    recipients: list[str] = field(default_factory=list)
    sent: bool = False
    send_error: str | None = None
    pruned: int = 0


def recipients() -> list[str]:
    s = get_settings()
    if s.alert_emails.strip():
        return [e.strip() for e in s.alert_emails.split(",") if e.strip()]
    with session_scope() as session:
        return list(session.scalars(select(User.email).where(User.is_admin.is_(True)).order_by(User.id)))


def run_monitor(*, dry_run: bool = False, notify: bool = True) -> MonitorReport:
    """Détecte, met à jour les alertes et notifie. `dry_run` : rien n'est enregistré ni envoyé."""
    s = get_settings()
    now = datetime.now(UTC)
    findings = detect(now)
    with session_scope() as session:
        open_rows = {
            a.key: a for a in session.scalars(select(MonitorAlert).where(MonitorAlert.resolved_at.is_(None)))
        }
    enabled = notify and s.alerts_enabled
    notified = {k: a.notified_at for k, a in open_rows.items()}
    p = plan(findings, notified, now, s.alert_remind_hours, retry_unnotified=enabled)
    report = MonitorReport(findings, p, {k: open_rows[k].message for k in p.resolved})
    if dry_run:
        report.recipients = recipients()
        return report

    if enabled and p.to_notify:
        report.recipients = recipients()
        if report.recipients:
            try:
                send_mail(report.recipients, *compose(report))
                report.sent = True
                audit(None, "alert.notify", ", ".join(f.key for f in p.opened + p.reminded) or "résolution")
            except Exception as exc:
                report.send_error = f"{type(exc).__name__} : {exc}"
                log.warning("Notification d'alerte impossible : %s", report.send_error)
        else:
            report.send_error = "Aucun destinataire (ni ALERT_EMAILS ni compte administrateur)."

    with session_scope() as session:
        for f in findings:
            row = session.get(MonitorAlert, f.key)
            # Une alerte résolue qui réapparaît est rouverte (nouvelle occurrence)
            if row is None or row.resolved_at is not None:
                if row is not None:
                    session.delete(row)
                    session.flush()
                row = MonitorAlert(key=f.key, opened_at=now)
                session.add(row)
            row.severity, row.message, row.last_seen_at = f.severity, f.message, now
            if report.sent and (f in p.opened or f in p.reminded):
                row.notified_at = now
        for key in p.resolved:
            if (row := session.get(MonitorAlert, key)) is not None:
                row.resolved_at = now
    report.pruned = prune_metrics(s.metrics_retention_days)
    return report


# --------------------------------------------------------------------------- #
# E-mail
# --------------------------------------------------------------------------- #


def compose(report: MonitorReport) -> tuple[str, str]:
    p = report.plan
    active = p.opened + p.reminded + p.ongoing
    critical = any(f.severity == "critical" for f in active)
    if active:
        subject = f"[GetIAJob] {'🔴' if critical else '🟠'} {len(active)} alerte(s) de supervision"
    else:
        subject = "[GetIAJob] ✅ Alertes résolues"
    lines: list[str] = []

    def section(title: str, items: list[Finding]) -> None:
        if items:
            lines.append(title)
            lines.extend(f"  - [{SEVERITY_LABELS.get(f.severity, f.severity)}] {f.message}" for f in items)
            lines.append("")

    section("Nouvelles alertes :", p.opened)
    section("Toujours en cours (rappel) :", p.reminded)
    section("Également en cours :", p.ongoing)
    if p.resolved:
        lines.append("Résolues :")
        lines.extend(f"  - {report.resolved_messages.get(k, k)}" for k in p.resolved)
        lines.append("")
    lines.append("Détails : Admin › Supervision dans GetIAJob.")
    return subject, "\n".join(lines)


def _smtp_settings() -> tuple[str, int, str, str]:
    s = get_settings()
    host = s.smtp_host or (
        s.imap_host.replace("imap.", "smtp.", 1) if s.imap_host.startswith("imap.") else ""
    )
    user = s.smtp_user or s.imap_user
    password = s.smtp_password or s.imap_password
    if "gmail" in host:
        password = password.replace(" ", "")  # mot de passe d'application Gmail affiché par blocs
    if not (host and user and password):
        raise RuntimeError("SMTP non configuré : renseigner SMTP_HOST/SMTP_USER/SMTP_PASSWORD (ou IMAP_*).")
    return host, s.smtp_port, user, password


def send_mail(to: list[str], subject: str, body: str, html: str | None = None) -> None:
    host, port, user, password = _smtp_settings()
    msg = EmailMessage()
    msg["From"] = f"GetIAJob <{user}>"
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(user, password)
            smtp.send_message(msg)
