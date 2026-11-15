"""Alertes e-mail APEC : extraction des offres et identification de l'utilisateur (mails synthétiques)."""

from __future__ import annotations

import base64
from email.message import EmailMessage

from getiajob.scraping.apec_mail import Mailbox, parse_alert

OFFERS = [
    ("179780467W", "Chef de projet informatique F/H", "ACME CONSULTING", "CDI", "Puteaux - 92"),
    ("179694894W", "Chef de Projet SAP F/H", "KAPA TEST", "CDD", "Paris 01 - 75"),
]
AUTH_OK = "mx.example.com; dkim=pass header.i=@example.org; spf=pass smtp.mailfrom=example.org"


def _link(num: str, slot: str) -> str:
    e = base64.urlsafe_b64encode(f"p1=www.apec.fr&p2={num}&p3=&xtor=EPR-41".encode()).decode().rstrip("=")
    return f"https://neomarket.diffusion.apec.fr/r/?id=h1,{slot}&e={e}&s=sig"


def _html() -> str:
    blocks = "".join(
        f'<tr><td><a href="{_link(n, "a")}">{t}</a><br><a href="{_link(n, "b")}">{c}</a><br>'
        f'<a href="{_link(n, "c")}">{k}&nbsp;•</a> <a href="{_link(n, "d")}">{loc}</a></td></tr>'
        for n, t, c, k, loc in OFFERS
    )
    return (
        "<html><body><p>2 offres correspondent à votre recherche : Chef de projet IT</p>"
        f'<table>{blocks}</table><a href="https://neomarket.diffusion.apec.fr/r/?id=x&e=cDE9d3d3">Gérer</a>'
        "</body></html>"
    )


def _mail(sender: str, *, auth: str | None = AUTH_OK, html: bool = True, **headers) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "alertes@getiajob.test"
    msg["Subject"] = "Fwd: 2 offres Apec du 14/11/2026"
    msg["Date"] = "Sat, 14 Nov 2026 19:38:04 +0100"
    msg["Message-ID"] = "<abc123@example.org>"
    if auth:
        msg["Authentication-Results"] = auth
    for key, value in headers.items():
        msg[key.replace("_", "-")] = value
    plain = "2 offres correspondent à votre recherche : Chef de projet IT\n\n" + "\n".join(
        f"\t{t} <{_link(n, 'a')}>\n{c} <{_link(n, 'b')}>\n{k} • <{_link(n, 'c')}>  {loc} <{_link(n, 'd')}>\n"
        for n, t, c, k, loc in OFFERS
    )
    msg.set_content(plain)
    if html:
        msg.add_alternative(_html(), subtype="html")
    return msg.as_bytes()


def test_manual_forward_is_attributed_to_sender():
    alert = parse_alert(_mail("Jane Doe <Jane.Doe@Example.org>"))
    assert alert.sender == "jane.doe@example.org"
    assert alert.verified and alert.is_alert
    assert alert.alert_name == "Chef de projet IT"
    assert len(alert.postings) == 2


def test_offer_fields_are_extracted():
    p = parse_alert(_mail("jane@example.org")).postings[0]
    assert p.source == "apec" and p.source_id == "179780467W"
    assert p.title == "Chef de projet informatique F/H"
    assert p.company == "ACME CONSULTING"
    assert p.location == "Puteaux - 92"
    assert p.criteria["Contrat"] == "CDI"
    assert p.url.endswith("/detail-offre/179780467W")
    assert p.posted_at.date().isoformat() == "2026-11-14"
    assert "Chef de projet informatique" in p.description and "apec.fr" in p.description


def test_plain_text_fallback():
    alert = parse_alert(_mail("jane@example.org", html=False))
    assert [p.company for p in alert.postings] == ["ACME CONSULTING", "KAPA TEST"]
    assert alert.postings[1].criteria["Contrat"] == "CDD"


def test_automatic_forward_uses_forwarding_address():
    raw = _mail("APEC <offres@diffusion.apec.fr>", X_Forwarded_For="jane@example.org alertes@getiajob.test")
    assert parse_alert(raw).sender == "jane@example.org"


def test_unauthenticated_sender_is_flagged():
    assert not parse_alert(_mail("jane@example.org", auth=None)).verified
    assert not parse_alert(_mail("jane@example.org", auth="mx; spf=fail; dkim=none")).verified


def test_regular_mail_is_not_an_alert():
    msg = EmailMessage()
    msg["From"] = "jane@example.org"
    msg["Subject"] = "Bonjour"
    msg.set_content("Rien à voir <https://example.org/?e=abc>")
    assert not parse_alert(msg.as_bytes()).is_alert


def test_gmail_app_password_spaces_removed():
    assert Mailbox("imap.gmail.com", "u", "abcd efgh ijkl mnop").password == "abcdefghijklmnop"
    assert Mailbox("mail.example.org", "u", "a b").password == "a b"


class FakeImap:
    def __init__(self, move_ok: bool = True, trash: bool = True):
        self.calls: list[tuple] = []
        self.move_ok, self.trash = move_ok, trash

    def list(self):
        folders = [b'(\\HasNoChildren) "/" "INBOX"']
        if self.trash:
            folders.append(b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Corbeille"')
        return "OK", folders

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "MOVE":
            return ("OK" if self.move_ok else "NO"), [None]
        return "OK", [None]

    def expunge(self):
        self.calls.append(("EXPUNGE",))
        return "OK", [None]


def _box(imap: FakeImap, readonly: bool = False) -> Mailbox:
    box = Mailbox("imap.gmail.com", "u", "p", readonly=readonly)
    box._imap = imap
    return box


def test_delete_moves_to_trash():
    imap = FakeImap()
    assert _box(imap).delete([b"12", b"15"]) == 2
    assert imap.calls == [("MOVE", "12,15", '"[Gmail]/Corbeille"')]


def test_delete_falls_back_to_copy_then_expunge():
    imap = FakeImap(move_ok=False)
    _box(imap).delete([b"7"])
    assert [c[0] for c in imap.calls] == ["MOVE", "COPY", "STORE", "EXPUNGE"]


def test_delete_without_trash_flags_and_expunges():
    imap = FakeImap(trash=False)
    _box(imap).delete([b"7"])
    assert [c[0] for c in imap.calls] == ["STORE", "EXPUNGE"]


def test_readonly_mailbox_never_deletes():
    import pytest

    with pytest.raises(RuntimeError):
        _box(FakeImap(), readonly=True).delete([b"1"])
