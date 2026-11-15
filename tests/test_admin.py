import pytest

from getiajob.services.admin import delete_user, set_admin
from getiajob.services.users import AuthError, SessionUser


def test_admin_cannot_change_or_delete_own_account():
    # Garde-fous vérifiés avant tout accès à la base
    with pytest.raises(AuthError):
        set_admin(actor_id=3, user_id=3, value=False)
    with pytest.raises(AuthError):
        delete_user(actor_id=3, user_id=3)


def test_session_user_is_not_admin_by_default():
    assert SessionUser(id=1, email="a@b.fr", full_name=None).is_admin is False
