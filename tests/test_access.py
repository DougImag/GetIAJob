import pytest

from getiajob.config import get_settings
from getiajob.services.access import consume_invitation, invitation_link, token_hash
from getiajob.services.users import AuthError


def test_token_hash_is_stable_and_trimmed():
    assert token_hash("abc") == token_hash("  abc\n")
    assert len(token_hash("abc")) == 64


def test_invitation_link_uses_public_url(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_url", "https://getiajob.example.fr/")
    assert invitation_link("T0K") == "https://getiajob.example.fr/connexion?invitation=T0K"
    monkeypatch.setattr(get_settings(), "public_url", "")
    assert invitation_link("T0K") is None


def test_password_signup_requires_a_code():
    # Refusé avant tout accès à la base
    with pytest.raises(AuthError, match="invitation"):
        consume_invitation(None, "a@b.fr", None)


def test_invitation_link_falls_back_to_app_url(monkeypatch):
    monkeypatch.setattr(get_settings(), "public_url", "")
    assert invitation_link("T0K", "http://localhost:8501") == "http://localhost:8501/connexion?invitation=T0K"
    monkeypatch.setattr(get_settings(), "public_url", "https://getiajob.example.fr")
    assert invitation_link("T0K", "http://localhost:8501").startswith("https://getiajob.example.fr/")


def test_invitation_html_has_signup_button():
    from datetime import datetime

    from getiajob.services.access import _invitation_html

    html = _invitation_html(
        "a@b.fr", "Léa <x>", "T0K", "https://g.fr/connexion?invitation=T0K", datetime(2026, 12, 10)
    )
    assert 'href="https://g.fr/connexion?invitation=T0K"' in html and "Créer mon compte" in html
    assert "Léa &lt;x&gt;" in html and "T0K" in html
