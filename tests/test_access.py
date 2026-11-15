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
