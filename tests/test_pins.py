from datetime import date

from getiajob.services import pins
from getiajob.services.pins import PinnedOffer, compose_pins_mail

OFFERS = [
    PinnedOffer(1, "AI Python Developer H/F", "Devoteam", "Paris", "https://example.com/1?a=1&b=2"),
    PinnedOffer(2, "Ingénieur IA <R&D>", None, None, "https://example.com/2"),
]


def test_compose_pins_mail():
    subject, text, html = compose_pins_mail(OFFERS, {1: 73.3}, today=date(2026, 11, 18))
    assert subject == "GetIAJob — 2 offres épinglées (18/11/2026)"
    assert (
        "1. AI Python Developer H/F\n   Devoteam · Paris · score 73/100\n   https://example.com/1?a=1&b=2"
        in text
    )
    assert "2. Ingénieur IA <R&D>\n   https://example.com/2" in text
    assert 'href="https://example.com/1?a=1&amp;b=2"' in html
    assert "Ingénieur IA &lt;R&amp;D&gt;" in html


def test_compose_single_offer_subject():
    subject, _, _ = compose_pins_mail(OFFERS[:1], today=date(2026, 11, 18))
    assert subject == "GetIAJob — 1 offre épinglée (18/11/2026)"


def test_send_pins(monkeypatch):
    sent = []
    monkeypatch.setattr(pins, "list_pins", lambda user_id: OFFERS)
    monkeypatch.setattr(pins, "send_mail", lambda to, subject, body, html: sent.append((to, subject)))
    assert pins.send_pins(3, "me@example.com") == 2
    assert sent[0][0] == ["me@example.com"]

    monkeypatch.setattr(pins, "list_pins", lambda user_id: [])
    assert pins.send_pins(3, "me@example.com") == 0
    assert len(sent) == 1
