"""Comptes utilisateurs (mot de passe bcrypt ou OIDC) et fiche utilisateur."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

import bcrypt
from sqlalchemy import delete, func, select

from getiajob.db import AuthSession, User, UserProfile, session_scope

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_BYTES = 72  # limite de bcrypt
# Hash factice : on vérifie quand même un mot de passe si l'email est inconnu (temps constant).
_DUMMY_HASH = bcrypt.hashpw(b"getiajob-dummy-password", bcrypt.gensalt()).decode()


class AuthError(ValueError):
    """Erreur d'authentification ou de validation présentable à l'utilisateur."""


@dataclass(frozen=True)
class SessionUser:
    id: int
    email: str
    full_name: str | None
    provider: str = "local"
    is_admin: bool = False

    @property
    def display_name(self) -> str:
        return self.full_name or self.email.split("@")[0]


@dataclass
class ProfileData:
    """Fiche utilisateur (ce qu'il cherche), découplée de l'ORM."""

    desired_job: str = ""
    locations: list[str] = field(default_factory=list)
    max_offer_age_days: int = 7  # fraîcheur des annonces : publiées depuis au plus N jours
    remote: list[str] = field(default_factory=list)
    contract_types: list[str] = field(default_factory=list)
    experience_levels: list[str] = field(default_factory=list)
    min_salary: int | None = None
    expectations: str = ""
    exclude_keywords: list[str] = field(default_factory=list)
    exclude_companies: list[str] = field(default_factory=list)
    sources: list[str] | None = None  # sources d'annonces ; None = toutes celles disponibles

    def completion(self) -> tuple[int, list[str]]:
        """Pourcentage de complétude et champs essentiels manquants."""
        checks = {
            "poste recherché": bool(self.desired_job.strip()),
            "lieu(x)": bool(self.locations),
            "mode de travail": bool(self.remote),
            "type de contrat": bool(self.contract_types),
        }
        missing = [name for name, ok in checks.items() if not ok]
        return round(100 * (len(checks) - len(missing)) / len(checks)), missing


def _to_session_user(user: User) -> SessionUser:
    return SessionUser(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        provider=user.auth_provider,
        is_admin=bool(user.is_admin),
    )


def _first_account(session) -> bool:
    """Le tout premier compte créé devient administrateur (sinon personne ne pourrait l'être)."""
    return not session.scalar(select(func.count(User.id)))


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères.")
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise AuthError(f"Le mot de passe ne doit pas dépasser {MAX_PASSWORD_BYTES} octets.")


def _check_signup(session, email: str, invitation: str | None, *, sso: bool = False) -> bool:
    """Règles d'inscription ; renvoie True pour le tout premier compte (administrateur).

    Adhésion sur invitation (INVITE_ONLY) : l'invitation est vérifiée et consommée dans la même
    transaction que la création du compte. Le premier compte d'une base vide en est dispensé.
    """
    from getiajob.config import get_settings
    from getiajob.services.access import consume_invitation

    first = _first_account(session)
    s = get_settings()
    if not first and not s.allow_signup:
        raise AuthError("La création de comptes est fermée sur cette instance.")
    if not first and s.invite_only:
        consume_invitation(session, email, invitation, sso=sso)
    return first


def create_user(
    email: str, password: str, full_name: str | None = None, invitation: str | None = None
) -> SessionUser:
    email = normalize_email(email)
    if not EMAIL_RE.match(email):
        raise AuthError("Adresse email invalide.")
    validate_password(password)
    with session_scope() as session:
        if session.scalar(select(User.id).where(User.email == email)):
            raise AuthError("Un compte existe déjà avec cette adresse email.")
        first = _check_signup(session, email, (invitation or "").strip() or None)
        user = User(
            email=email,
            full_name=(full_name or "").strip() or None,
            password_hash=hash_password(password),
            auth_provider="local",
            is_admin=first,
            last_login_at=datetime.now(UTC),
        )
        session.add(user)
        session.flush()
        return _to_session_user(user)


def authenticate(email: str, password: str) -> SessionUser | None:
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == normalize_email(email)))
        if user is None or not user.password_hash:
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, user.password_hash):
            return None
        user.last_login_at = datetime.now(UTC)
        return _to_session_user(user)


def get_or_create_oidc_user(email: str, full_name: str | None, provider: str = "oidc") -> SessionUser:
    email = normalize_email(email)
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            first = _check_signup(session, email, None, sso=True)
            user = User(email=email, full_name=full_name, auth_provider=provider, is_admin=first)
            session.add(user)
        user.last_login_at = datetime.now(UTC)
        session.flush()
        return _to_session_user(user)


def update_account(user_id: int, full_name: str) -> SessionUser:
    with session_scope() as session:
        user = session.get(User, user_id)
        user.full_name = full_name.strip() or None
        return _to_session_user(user)


def change_password(user_id: int, current: str, new: str) -> None:
    validate_password(new)
    with session_scope() as session:
        user = session.get(User, user_id)
        if not user.password_hash or not verify_password(current, user.password_hash):
            raise AuthError("Mot de passe actuel incorrect.")
        user.password_hash = hash_password(new)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_auth_session(user_id: int, days: int) -> str:
    """Crée une session persistante et renvoie le jeton en clair (à placer dans le cookie)."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    with session_scope() as session:
        session.execute(delete(AuthSession).where(AuthSession.expires_at < now))  # ménage
        session.add(
            AuthSession(user_id=user_id, token_hash=_token_hash(token), expires_at=now + timedelta(days=days))
        )
    return token


def user_from_token(token: str) -> SessionUser | None:
    with session_scope() as session:
        user = session.scalar(
            select(User)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(AuthSession.token_hash == _token_hash(token), AuthSession.expires_at > datetime.now(UTC))
        )
        return _to_session_user(user) if user else None


def revoke_auth_session(token: str) -> None:
    with session_scope() as session:
        session.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))


def count_users() -> int:
    with session_scope() as session:
        return session.scalar(select(func.count(User.id))) or 0


def get_profile(user_id: int) -> ProfileData:
    with session_scope() as session:
        row = session.get(UserProfile, user_id)
        if row is None:
            return ProfileData()
        return ProfileData(**{k: getattr(row, k) for k in ProfileData.__dataclass_fields__})


# Options du formulaire de recherche qui reprennent une valeur de la fiche : oubliées quand la fiche
# est enregistrée, pour que ses nouvelles valeurs s'appliquent à la recherche suivante.
PROFILE_SEARCH_OPTIONS = frozenset(
    {
        "max_age_days",
        "locations",
        "sources",
        "remote",
        "experience_levels",
        "exclude_keywords",
        "exclude_companies",
    }
)


def save_profile(user_id: int, data: ProfileData) -> None:
    with session_scope() as session:
        row = session.get(UserProfile, user_id) or UserProfile(user_id=user_id)
        for key, value in asdict(data).items():
            setattr(row, key, value)
        if row.search_options:
            row.search_options = {
                k: v for k, v in row.search_options.items() if k not in PROFILE_SEARCH_OPTIONS
            } or None
        session.add(row)
