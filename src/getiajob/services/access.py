"""Adhésion sur invitation : demandes d'accès, invitations et contrôle à l'inscription.

Parcours : un visiteur remplit le formulaire de demande (accueil ou page de connexion) → les
administrateurs sont prévenus par e-mail → un administrateur accepte (ou invite directement une
adresse) → un code d'invitation est envoyé par e-mail → le compte est créé avec ce code et la
même adresse. Le code n'est stocké que sous forme de hash SHA-256 et sert une seule fois.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from getiajob.config import get_settings
from getiajob.db import AccessRequest, User, session_scope
from getiajob.services.users import EMAIL_RE, AuthError, normalize_email

log = logging.getLogger(__name__)

PENDING, INVITED, USED, REJECTED, REVOKED = "pending", "invited", "used", "rejected", "revoked"
STATUS_LABELS = {
    PENDING: "À traiter",
    INVITED: "Invitation envoyée",
    USED: "Compte créé",
    REJECTED: "Refusée",
    REVOKED: "Invitation annulée",
    "expired": "Invitation expirée",
}
MAX_MESSAGE = 1000
MAX_REQUESTS_PER_HOUR = 20  # garde-fou anti-spam (toutes adresses confondues)


@dataclass(frozen=True)
class RequestRow:
    id: int
    email: str
    full_name: str | None
    message: str | None
    status: str  # statut affiché : « expired » pour une invitation périmée
    created_at: datetime
    decided_at: datetime | None
    expires_at: datetime | None
    used_at: datetime | None

    @property
    def display_name(self) -> str:
        return self.full_name or self.email.split("@")[0]


@dataclass(frozen=True)
class Invitation:
    email: str
    token: str
    link: str | None
    expires_at: datetime
    mailed: bool
    mail_error: str | None = None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


def invitation_link(token: str) -> str | None:
    base = get_settings().public_url.strip().rstrip("/")
    return f"{base}/connexion?invitation={token}" if base else None


def _now() -> datetime:
    return datetime.now(UTC)


def _is_expired(row: AccessRequest, now: datetime) -> bool:
    return row.expires_at is not None and row.expires_at < now


def _display_status(row: AccessRequest, now: datetime) -> str:
    return "expired" if row.status == INVITED and _is_expired(row, now) else row.status


# --------------------------------------------------------------------------- #
# Règles d'inscription
# --------------------------------------------------------------------------- #


def invitation_required() -> bool:
    """Faut-il un code pour créer un compte ? (jamais pour le tout premier compte)."""
    s = get_settings()
    if not (s.allow_signup and s.invite_only):
        return False
    with session_scope() as session:
        return bool(session.scalar(select(func.count(User.id))))


def requests_open() -> bool:
    """Le formulaire de demande d'accès a-t-il un sens sur cette instance ?"""
    s = get_settings()
    return s.allow_signup and s.invite_only


def check_invitation(token: str) -> tuple[str, str | None] | None:
    """(email, nom) d'une invitation valide, pour pré-remplir le formulaire d'inscription."""
    if not token or not token.strip():
        return None
    with session_scope() as session:
        row = session.scalar(select(AccessRequest).where(AccessRequest.token_hash == token_hash(token)))
        if row is None or row.status != INVITED or _is_expired(row, _now()):
            return None
        return row.email, row.full_name


def consume_invitation(session, email: str, token: str | None, *, sso: bool = False) -> None:
    """Vérifie et consomme l'invitation lors de la création d'un compte (même transaction).

    `token` : code saisi (inscription par mot de passe, obligatoire). En SSO, l'adresse est déjà
    prouvée par le fournisseur d'identité : il suffit d'une invitation valide pour cette adresse.
    """
    now = _now()
    if not sso and not token:
        raise AuthError("L'inscription se fait sur invitation : saisis le code reçu par e-mail.")
    if token:
        row = session.scalar(select(AccessRequest).where(AccessRequest.token_hash == token_hash(token)))
        if row is None or row.status != INVITED:
            raise AuthError("Code d'invitation invalide ou déjà utilisé.")
        if _is_expired(row, now):
            raise AuthError("Cette invitation a expiré : demande-en une nouvelle.")
        if row.email != email:
            raise AuthError("Ce code d'invitation a été envoyé à une autre adresse email.")
    else:
        row = session.scalar(
            select(AccessRequest)
            .where(AccessRequest.email == email, AccessRequest.status == INVITED)
            .where((AccessRequest.expires_at.is_(None)) | (AccessRequest.expires_at >= now))
            .order_by(AccessRequest.id.desc())
        )
        if row is None:
            raise AuthError(
                "L'inscription se fait sur invitation : demande un accès depuis la page d'accueil."
            )
    row.status = USED
    row.used_at = now


# --------------------------------------------------------------------------- #
# Demandes d'accès (public)
# --------------------------------------------------------------------------- #


def submit_request(full_name: str, email: str, message: str = "") -> None:
    """Enregistre une demande d'accès et prévient les administrateurs.

    Une demande déjà en attente pour la même adresse est mise à jour (pas de doublon).
    """
    full_name, email, message = full_name.strip(), normalize_email(email), message.strip()
    if not full_name:
        raise AuthError("Indique ton nom.")
    if not EMAIL_RE.match(email):
        raise AuthError("Adresse email invalide.")
    if len(message) > MAX_MESSAGE:
        raise AuthError(f"Message trop long ({MAX_MESSAGE} caractères maximum).")
    now = _now()
    with session_scope() as session:
        if session.scalar(select(User.id).where(User.email == email)):
            raise AuthError("Un compte existe déjà avec cette adresse email : connecte-toi.")
        existing = session.scalar(
            select(AccessRequest).where(
                AccessRequest.email == email, AccessRequest.status.in_([PENDING, INVITED])
            )
        )
        if existing is not None and existing.status == INVITED and not _is_expired(existing, now):
            raise AuthError(
                "Une invitation t'a déjà été envoyée à cette adresse : vérifie ta boîte de réception."
            )
        if existing is not None and existing.status == PENDING:
            existing.full_name, existing.message = full_name, message or existing.message
            return
        recent = session.scalar(
            select(func.count(AccessRequest.id)).where(AccessRequest.created_at >= now - timedelta(hours=1))
        )
        if recent >= MAX_REQUESTS_PER_HOUR:
            raise AuthError("Beaucoup de demandes en ce moment : réessaie dans une heure.")
        session.add(AccessRequest(email=email, full_name=full_name, message=message or None))

    from getiajob.services.monitoring import audit

    audit(None, "access_request", email, full_name)
    _notify_admins_async(full_name, email, message)


def _notify_admins_async(full_name: str, email: str, message: str) -> None:
    """Prévient les administrateurs sans bloquer l'interface (l'envoi SMTP peut être lent)."""

    def send() -> None:
        from getiajob.services.alerting import recipients, send_mail

        try:
            to = recipients()
            if not to:
                return
            lines = [
                "Nouvelle demande d'accès à GetIAJob :",
                "",
                f"  Nom   : {full_name}",
                f"  Email : {email}",
            ]
            if message:
                lines += ["", "Message :", message]
            lines += ["", "À traiter dans Admin › Utilisateurs › Demandes d'accès."]
            if base := get_settings().public_url.strip().rstrip("/"):
                lines.append(f"{base}/admin-utilisateurs")
            send_mail(to, f"[GetIAJob] Demande d'accès de {full_name}", "\n".join(lines))
        except Exception as exc:  # noqa: BLE001 - la demande est enregistrée quoi qu'il arrive
            log.warning("Notification de demande d'accès non envoyée : %s", exc)

    threading.Thread(target=send, daemon=True, name="access-request-mail").start()


# --------------------------------------------------------------------------- #
# Administration
# --------------------------------------------------------------------------- #


def _require_admin(session, actor_id: int) -> None:
    if not session.scalar(select(User.is_admin).where(User.id == actor_id)):
        raise AuthError("Action réservée aux administrateurs.")


def list_requests() -> list[RequestRow]:
    now = _now()
    with session_scope() as session:
        rows = session.scalars(select(AccessRequest).order_by(AccessRequest.created_at.desc())).all()
        return [
            RequestRow(
                id=r.id,
                email=r.email,
                full_name=r.full_name,
                message=r.message,
                status=_display_status(r, now),
                created_at=r.created_at,
                decided_at=r.decided_at,
                expires_at=r.expires_at,
                used_at=r.used_at,
            )
            for r in rows
        ]


def pending_count() -> int:
    with session_scope() as session:
        return session.scalar(select(func.count(AccessRequest.id)).where(AccessRequest.status == PENDING))


def invite(
    actor_id: int, email: str, full_name: str | None = None, request_id: int | None = None
) -> Invitation:
    """Crée (ou renouvelle) une invitation et l'envoie par e-mail.

    `request_id` : demande acceptée ou invitation à renvoyer ; sinon invitation directe d'une adresse
    (une demande en attente pour cette adresse est alors réutilisée).
    """
    email = normalize_email(email)
    if not EMAIL_RE.match(email):
        raise AuthError("Adresse email invalide.")
    token = secrets.token_urlsafe(24)
    now = _now()
    expires = now + timedelta(days=max(1, get_settings().invitation_days))
    with session_scope() as session:
        _require_admin(session, actor_id)
        if session.scalar(select(User.id).where(User.email == email)):
            raise AuthError("Un compte existe déjà avec cette adresse email.")
        if request_id is not None:
            row = session.get(AccessRequest, request_id)
            if row is None or row.email != email:
                raise AuthError("Demande introuvable.")
        else:
            row = session.scalar(
                select(AccessRequest)
                .where(AccessRequest.email == email, AccessRequest.status.in_([PENDING, INVITED]))
                .order_by(AccessRequest.id.desc())
            )
            if row is None:
                row = AccessRequest(email=email)
                session.add(row)
        if full_name and full_name.strip():
            row.full_name = full_name.strip()
        row.status, row.token_hash = INVITED, token_hash(token)
        row.decided_at, row.decided_by_id, row.expires_at, row.used_at = now, actor_id, expires, None
        name = row.full_name

    from getiajob.services.monitoring import audit

    audit(actor_id, "invitation", email)
    link = invitation_link(token)
    try:
        _send_invitation(email, name, token, link, expires)
    except Exception as exc:  # noqa: BLE001 - l'administrateur peut transmettre le lien lui-même
        log.warning("Invitation non envoyée à %s : %s", email, exc)
        return Invitation(email, token, link, expires, mailed=False, mail_error=str(exc))
    return Invitation(email, token, link, expires, mailed=True)


def _send_invitation(email: str, name: str | None, token: str, link: str | None, expires: datetime) -> None:
    from zoneinfo import ZoneInfo

    from getiajob.services.alerting import send_mail

    until = expires.astimezone(ZoneInfo(get_settings().display_timezone))
    lines = [f"Bonjour{f' {name}' if name else ''},", "", "Tu es invité(e) à rejoindre GetIAJob.", ""]
    if link:
        lines += [f"Crée ton compte avec l'adresse {email} en suivant ce lien :", link, ""]
        lines += ["Si le lien ne s'ouvre pas, va sur la page de connexion, onglet « Créer un compte »,"]
    else:
        lines += ["Va sur la page de connexion de GetIAJob, onglet « Créer un compte »,"]
    lines += [
        f"et saisis ton adresse ({email}) avec ce code d'invitation :",
        "",
        f"    {token}",
        "",
        f"Cette invitation est valable jusqu'au {until:%d/%m/%Y à %H:%M} et ne sert qu'une fois.",
        "",
        "À bientôt sur GetIAJob !",
    ]
    send_mail([email], "Ton invitation à GetIAJob", "\n".join(lines))


def _decide(actor_id: int, request_id: int, allowed: tuple[str, ...], status: str, action: str) -> None:
    with session_scope() as session:
        _require_admin(session, actor_id)
        row = session.get(AccessRequest, request_id)
        if row is None or row.status not in allowed:
            raise AuthError("Cette demande a déjà été traitée.")
        row.status, row.token_hash = status, None
        row.decided_at, row.decided_by_id = _now(), actor_id
        email = row.email

    from getiajob.services.monitoring import audit

    audit(actor_id, action, email)


def reject(actor_id: int, request_id: int) -> None:
    _decide(actor_id, request_id, (PENDING,), REJECTED, "access_request_rejected")


def revoke(actor_id: int, request_id: int) -> None:
    _decide(actor_id, request_id, (INVITED,), REVOKED, "invitation_revoked")
