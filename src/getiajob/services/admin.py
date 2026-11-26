"""Administration : liste des comptes, rôle administrateur, suppression d'un compte."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select

from getiajob.db import CVRecord, JobFeedback, LLMUsage, SearchSession, User, session_scope
from getiajob.services.monitoring import audit
from getiajob.services.users import AuthError, SessionUser, _to_session_user


@dataclass(frozen=True)
class UserRow:
    id: int
    email: str
    full_name: str | None
    provider: str
    is_admin: bool
    created_at: datetime
    last_login_at: datetime | None
    n_cvs: int
    n_searches: int
    n_feedback: int
    token_limit: int | None = None  # limite mensuelle de tokens (None : aucune)
    month_tokens: int = 0  # tokens consommés depuis le 1er du mois

    @property
    def display_name(self) -> str:
        return self.full_name or self.email.split("@")[0]


def is_admin(user_id: int) -> bool:
    """Rôle lu en base à chaque appel : un retrait de droits s'applique sans reconnexion."""
    with session_scope() as session:
        return bool(session.scalar(select(User.is_admin).where(User.id == user_id)))


def _counts(session, model) -> dict[int, int]:
    rows = session.execute(select(model.user_id, func.count()).group_by(model.user_id)).all()
    return {user_id: n for user_id, n in rows if user_id is not None}


def list_users() -> list[UserRow]:
    with session_scope() as session:
        cvs, searches, feedback = (_counts(session, m) for m in (CVRecord, SearchSession, JobFeedback))
        users = session.scalars(select(User).order_by(User.created_at)).all()
        tokens = _month_tokens(session)
        return [
            UserRow(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                provider=u.auth_provider,
                is_admin=bool(u.is_admin),
                created_at=u.created_at,
                last_login_at=u.last_login_at,
                n_cvs=cvs.get(u.id, 0),
                n_searches=searches.get(u.id, 0),
                n_feedback=feedback.get(u.id, 0),
                token_limit=u.token_limit,
                month_tokens=tokens.get(u.id, 0),
            )
            for u in users
        ]


def _month_tokens(session) -> dict[int, int]:
    """Tokens consommés depuis le 1er du mois, par utilisateur."""
    from getiajob.services.llm_settings import period_start

    rows = session.execute(
        select(LLMUsage.user_id, func.sum(LLMUsage.input_tokens + LLMUsage.output_tokens))
        .where(LLMUsage.user_id.is_not(None), LLMUsage.created_at >= period_start("month"))
        .group_by(LLMUsage.user_id)
    ).all()
    return {user_id: int(n or 0) for user_id, n in rows}


def set_token_limit(actor_id: int, user_id: int, tokens: int | None) -> None:
    """Fixe (ou retire, `tokens` None) la limite mensuelle de tokens d'un utilisateur."""
    if tokens is not None and tokens <= 0:
        raise AuthError("La limite doit être un nombre de tokens positif.")
    with session_scope() as session:
        _require_admin(session, actor_id)
        user = session.get(User, user_id)
        if user is None:
            raise AuthError("Compte introuvable.")
        user.token_limit, email = tokens, user.email
    label = f"{tokens:,} tokens par mois".replace(",", " ") if tokens else "aucune limite"
    audit(actor_id, "user.token_limit", email, label)


def _require_admin(session, actor_id: int) -> None:
    if not session.scalar(select(User.is_admin).where(User.id == actor_id)):
        raise AuthError("Action réservée aux administrateurs.")


def set_admin(actor_id: int, user_id: int, value: bool) -> None:
    """Donne ou retire le rôle administrateur. On ne peut pas modifier son propre rôle."""
    if actor_id == user_id:
        raise AuthError("Tu ne peux pas modifier ton propre rôle.")
    with session_scope() as session:
        _require_admin(session, actor_id)
        user = session.get(User, user_id)
        if user is None:
            raise AuthError("Compte introuvable.")
        user.is_admin = value
        email = user.email
    audit(actor_id, "user.admin_grant" if value else "user.admin_revoke", email)


def delete_user(actor_id: int, user_id: int) -> None:
    """Supprime un compte et toutes ses données (CV, fiche, recherches, avis, sessions)."""
    if actor_id == user_id:
        raise AuthError("Tu ne peux pas supprimer ton propre compte depuis l'administration.")
    with session_scope() as session:
        _require_admin(session, actor_id)
        target = session.get(User, user_id)
        if target is None:
            raise AuthError("Compte introuvable.")
        email = target.email
        # Suppression SQL directe : les clés étrangères ON DELETE CASCADE emportent les données liées
        session.execute(delete(User).where(User.id == user_id))
    audit(actor_id, "user.delete", email)


def impersonation_target(actor_id: int, user_id: int) -> SessionUser:
    """Compte à consulter en lecture seule par un administrateur (tracé dans le journal d'audit)."""
    if actor_id == user_id:
        raise AuthError("C'est déjà ton compte.")
    with session_scope() as session:
        _require_admin(session, actor_id)
        user = session.get(User, user_id)
        if user is None:
            raise AuthError("Compte introuvable.")
        target = _to_session_user(user)
    audit(actor_id, "user.impersonate", target.email, "consultation en lecture seule")
    return target


def impersonation_write_mode(actor_id: int, target_email: str, enabled: bool) -> None:
    """Autorise (ou retire) les modifications pendant une consultation ; tracé dans le journal d'audit."""
    if enabled:
        with session_scope() as session:
            _require_admin(session, actor_id)
    audit(
        actor_id,
        "user.impersonate_write" if enabled else "user.impersonate_readonly",
        target_email,
        "modifications autorisées" if enabled else "retour en lecture seule",
    )


def end_impersonation(actor_id: int, target_email: str) -> None:
    audit(actor_id, "user.impersonate_end", target_email)
