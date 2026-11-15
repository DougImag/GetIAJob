"""Supervision : cycle de vie des alertes, e-mail de notification, mesures HTTP (sans base ni réseau)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from getiajob.services import alerting, monitoring
from getiajob.services.alerting import Finding, MonitorReport, compose, plan

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
A = Finding("source.linkedin.failing", "critical", "LinkedIn : 3 échecs consécutifs.")
B = Finding("mail.stale", "warning", "Relève des alertes e-mail depuis 2 h.")


def test_new_finding_is_opened():
    p = plan([A], {}, NOW, 24)
    assert p.opened == [A] and not p.reminded and not p.resolved and p.to_notify


def test_recent_alert_is_not_renotified():
    p = plan([A], {A.key: NOW - timedelta(hours=2)}, NOW, 24)
    assert p.ongoing == [A] and not p.to_notify


def test_old_alert_is_reminded():
    p = plan([A], {A.key: NOW - timedelta(hours=25)}, NOW, 24)
    assert p.reminded == [A]
    # Jamais notifiée (ex. envoi en échec) : on retente
    assert plan([A], {A.key: None}, NOW, 24).reminded == [A]
    # … sauf si les notifications sont coupées
    assert plan([A], {A.key: None}, NOW, 24, retry_unnotified=False).ongoing == [A]


def test_vanished_alert_is_resolved():
    p = plan([B], {A.key: NOW}, NOW, 24)
    assert p.opened == [B] and p.resolved == [A.key]


def test_mail_lists_sections():
    p = plan([A, B], {B.key: NOW - timedelta(hours=1), "backup.stale": NOW}, NOW, 24)
    subject, body = compose(MonitorReport([A, B], p, {"backup.stale": "Dernière sauvegarde il y a 3 j."}))
    assert "2 alerte(s)" in subject and "🔴" in subject
    assert "Nouvelles alertes" in body and "LinkedIn" in body
    assert "Également en cours" in body and "Relève" in body
    assert "Résolues" in body and "sauvegarde" in body


def test_all_resolved_mail():
    subject, body = compose(MonitorReport([], plan([], {A.key: NOW}, NOW, 24), {A.key: A.message}))
    assert "résolues" in subject.lower() and A.message in body


def test_smtp_derived_from_imap(monkeypatch):
    from getiajob.config import Settings

    settings = Settings(imap_host="imap.gmail.com", imap_user="box@gmail.com", imap_password="abcd efgh")
    monkeypatch.setattr(alerting, "get_settings", lambda: settings)
    assert alerting._smtp_settings() == ("smtp.gmail.com", 465, "box@gmail.com", "abcdefgh")


def test_http_hooks_record_status(monkeypatch):
    seen = []
    monkeypatch.setattr(monitoring, "record", lambda *a, **k: seen.append((a, k)))
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(429)),
        event_hooks=monitoring.http_hooks("linkedin"),
    )
    client.get("https://example.org/jobs")
    ((args, kwargs),) = seen
    assert args == ("http", "linkedin") and kwargs["ok"] is False and kwargs["detail"] == "429"


def test_timed_records_failure_and_reraises(monkeypatch):
    import pytest

    seen = []
    monkeypatch.setattr(monitoring, "record", lambda *a, **k: seen.append((a, k)))
    with pytest.raises(ValueError):
        with monitoring.timed("llm", "evaluation"):
            raise ValueError("boom")
    assert seen[0][1]["ok"] is False and "boom" in seen[0][1]["detail"]


def test_record_never_raises(monkeypatch):
    def broken():
        raise RuntimeError("base injoignable")

    monkeypatch.setattr(monitoring, "session_scope", broken)
    monitoring.record("llm", "x")  # aucune exception
