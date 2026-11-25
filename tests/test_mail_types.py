import base64
from email.message import EmailMessage

from getiajob import llm
from getiajob.schemas import MailOffer, MailOffers
from getiajob.scraping.apec_mail import mail_domain, parse_alert
from getiajob.scraping.mail_offers import extract_offers, numbered_text, resolve_link
from getiajob.services.mail_alerts import type_label

AUTH = "mx.google.com; dkim=pass header.i=@example.org; spf=pass"
TARGET = "https://www.free-work.com/fr/tech-it/job-mission/chef-de-projet-ia?utm_source=mailjet&id=7"


def _tracked(url: str) -> str:
    seg = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"https://0pxgj.mjt.lu/lnk/EAAAC/3/ilbX/{seg}"


def _mail(sender: str, subject: str, body_prefix: str = "") -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "alertes@getiajob.test"
    msg["Subject"] = subject
    msg["Message-ID"] = "<fw1@example.org>"
    msg["Authentication-Results"] = AUTH
    links = "".join(
        f'<p>Freelance - CDI</p><p>{t}</p><p>Île-de-France</p><a href="{_tracked(u)}">Postuler</a>'
        for t, u in (("Chef de projet IA", TARGET), ("Dév. Java", "https://www.free-work.com/fr/job/java"))
    )
    msg.set_content(body_prefix + "Offres")
    msg.add_alternative(
        f"<html><body>{body_prefix.replace(chr(10), '<br>')}{links}"
        '<a href="https://www.free-work.com/fr/tech-it/jobs">Voir toutes les offres</a>'
        '<a href="https://free-work.com/unsubscribe?u=1">Se désinscrire</a></body></html>',
        subtype="html",
    )
    return msg.as_bytes()


FORWARD = "Début du message réexpédié :\n\nDe: Free-Work <contact@free-work.com>\nObjet: Nouvelles offres\n\n"


def test_manual_forward_origin():
    alert = parse_alert(
        _mail("Jane <jane@example.org>", "Fwd: Nouvelles offres", FORWARD), {"jane@example.org"}
    )
    assert alert.candidates == ["jane@example.org"]
    assert (alert.origin, alert.origin_name, alert.mail_type) == (
        "contact@free-work.com",
        "Free-Work",
        "free-work.com",
    )


def test_automatic_forward_origin():
    raw = _mail("Free-Work <news@mail.free-work.com>", "Nouvelles offres")
    raw = raw.replace(b"From:", b"X-Forwarded-For: jane@example.org\nFrom:", 1)
    alert = parse_alert(raw, {"jane@example.org"})
    assert alert.mail_type == "free-work.com"
    assert "jane@example.org" in alert.candidates


def test_mail_domain_and_label():
    assert mail_domain("x@alerts.pole-emploi.gouv.fr") == "pole-emploi.gouv.fr"
    assert mail_domain("a@apec.fr") == "apec.fr"
    assert type_label("apec.fr", "Apec") == "APEC"
    assert type_label("free-work.com", None) == "free-work.com"


def test_resolve_tracking_link():
    assert (
        resolve_link(_tracked(TARGET))
        == "https://www.free-work.com/fr/tech-it/job-mission/chef-de-projet-ia?id=7"
    )
    assert resolve_link("https://t.example/c?url=https%3A%2F%2Fjobs.example%2F1") == "https://jobs.example/1"


def test_numbered_text_skips_unsubscribe():
    alert = parse_alert(_mail("Jane <jane@example.org>", "Fwd: x", FORWARD), {"jane@example.org"})
    text, links = numbered_text(alert.message)
    assert "Postuler [L1]" in text and "Postuler [L2]" in text
    assert not any("unsubscribe" in u for u in links)
    assert links[0].endswith("chef-de-projet-ia?id=7")


def test_extract_offers_with_llm(monkeypatch):
    class Fake:
        def invoke(self, messages):
            assert "[L1]" in messages[1][1]
            return MailOffers(
                offers=[
                    MailOffer(
                        title="Chef de projet IA",
                        company="Free-Work",
                        location="Île-de-France",
                        contract="Freelance - CDI",
                        salary="40k - 60k €/an\n400 - 650 €/j",
                        link=1,
                    ),
                    MailOffer(title="Doublon", link=1),
                    MailOffer(title="Lien inventé", link=42),
                ]
            )

    monkeypatch.setattr(llm, "structured", lambda schema: Fake())
    alert = parse_alert(_mail("Jane <jane@example.org>", "Fwd: x", FORWARD), {"jane@example.org"})
    offers = extract_offers(alert.message, "Free-Work", None)
    assert [o.title for o in offers] == ["Chef de projet IA"]
    o = offers[0]
    assert o.source == "mail" and o.external_id.startswith("mail:")
    assert o.criteria == {"Origine": "Alerte e-mail Free-Work", "Contrat": "Freelance - CDI"}
    assert o.company is None and o.salary == "40k - 60k €/an · 400 - 650 €/j"


def test_import_cooldown(monkeypatch):
    from contextlib import contextmanager
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    import pytest

    from getiajob.services import mail_alerts
    from getiajob.services.mail_alerts import SyncReport

    state = {"last": None}

    class Session:
        def scalar(self, query):
            return state["last"]

        def execute(self, stmt):
            # UPDATE … WHERE (dernier import absent ou trop ancien, sauf admin) RETURNING id
            params = stmt.compile().params
            sets = params.get("last_mail_import_at")
            limit = next((v for k, v in params.items() if k.startswith("last_mail_import_at_")), None)
            ok = limit is None or state["last"] is None or state["last"] <= limit
            if ok and sets is not None:
                state["last"] = sets
            return SimpleNamespace(scalar=lambda: 1 if ok else None)

    @contextmanager
    def scope():
        yield Session()

    monkeypatch.setattr(mail_alerts, "session_scope", scope)
    monkeypatch.setattr(mail_alerts, "sync_mailbox", lambda: SyncReport())
    mail_alerts.import_for_user(3)
    with pytest.raises(mail_alerts.ImportThrottled):
        mail_alerts.import_for_user(3)
    assert mail_alerts.next_import_at(3) is not None
    mail_alerts.import_for_user(3, admin=True)  # administrateur : sans limite
    assert mail_alerts.next_import_at(3, admin=True) is None
    state["last"] = datetime.now(UTC) - timedelta(minutes=6)
    mail_alerts.import_for_user(3)
