"""Schéma PostgreSQL (+ pgvector pour la similarité sémantique)."""

from __future__ import annotations

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, deferred, mapped_column, relationship

from getiajob.config import get_settings

EMBEDDING_DIM = get_settings().embedding_dim


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# Utilisateurs (interface web)
# --------------------------------------------------------------------------- #


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))  # None pour un compte OIDC
    auth_provider: Mapped[str] = mapped_column(String(20), default="local")
    # Administrateur : accès au menu « Admin » (gestion des utilisateurs)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Dernier import des alertes e-mail lancé à la main (limité à un toutes les 5 min hors admin)
    last_mail_import_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[UserProfile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class AccessRequest(Base):
    """Demande d'accès (formulaire public) et invitation qui en découle.

    Statuts : pending (demande à traiter), invited (invitation envoyée, en attente d'inscription),
    used (compte créé), rejected (demande refusée), revoked (invitation annulée).
    Une invitation peut aussi être créée directement par un administrateur (sans demande).
    Seul le hash SHA-256 du code d'invitation est stocké.
    """

    __tablename__ = "access_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    """Session « rester connecté » : seul le hash SHA-256 du jeton (cookie) est stocké."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserProfile(Base):
    """Fiche de recherche : poste recherché, lieux, fraîcheur des annonces, attentes."""

    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    desired_job: Mapped[str] = mapped_column(String(255), default="")
    locations: Mapped[list] = mapped_column(JSONB, default=list)
    max_offer_age_days: Mapped[int] = mapped_column(Integer, default=7)  # fraîcheur des annonces
    remote: Mapped[list] = mapped_column(JSONB, default=list)
    contract_types: Mapped[list] = mapped_column(JSONB, default=list)
    experience_levels: Mapped[list] = mapped_column(JSONB, default=list)
    min_salary: Mapped[int | None] = mapped_column(Integer)
    expectations: Mapped[str] = mapped_column(Text, default="")
    exclude_keywords: Mapped[list] = mapped_column(JSONB, default=list)
    exclude_companies: Mapped[list] = mapped_column(JSONB, default=list)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # None = toutes les sources
    # Dernières valeurs du formulaire « Nouvelle recherche » (reprises à la recherche suivante)
    search_options: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="profile")


# --------------------------------------------------------------------------- #
# CV, offres, scores
# --------------------------------------------------------------------------- #


class CVRecord(Base):
    """Un CV (fichier + texte + profil extrait). `user_id` NULL = CV importé via la CLI."""

    __tablename__ = "cv_profiles"
    __table_args__ = (UniqueConstraint("user_id", "file_hash", name="uq_cv_user_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    file_name: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    file_data: Mapped[bytes | None] = deferred(mapped_column(LargeBinary, nullable=True))
    raw_text: Mapped[str] = mapped_column(Text)
    profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # None tant que non analysé
    # Analyse en tâche de fond : pending / running / done / failed (None = jamais demandée)
    analysis_status: Mapped[str | None] = mapped_column(String(20))
    analysis_error: Mapped[str | None] = mapped_column(Text)
    analysis_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Avancement de l'analyse en cours : {"workers": n, "steps": {étape: état}, "started": {étape: iso}}
    analysis_progress: Mapped[dict | None] = mapped_column(JSONB)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    matches: Mapped[list[JobMatch]] = relationship(back_populates="cv", cascade="all, delete-orphan")


class JobOffer(Base):
    """Une offre d'emploi. `last_seen_at` = dernière fois qu'elle est remontée dans une recherche.

    `external_id` (colonne historique `linkedin_id`) : identifiant chez la source, préfixé hors
    LinkedIn (`ft:…`, `adz:…`). `fingerprint` : empreinte titre + entreprise (doublons entre sources).
    """

    __tablename__ = "job_offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column("linkedin_id", String(32), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(20), default="linkedin", server_default="linkedin")
    fingerprint: Mapped[str | None] = mapped_column(String(40), index=True)
    # Offre privée (reçue par alerte e-mail d'un utilisateur) : visible de lui seul. None = publique.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(500))
    company: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(1000))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(Text)
    salary: Mapped[str | None] = mapped_column(String(255))
    criteria: Mapped[dict] = mapped_column(JSONB, default=dict)
    search_query: Mapped[str | None] = mapped_column(String(255))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    matches: Mapped[list[JobMatch]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobMatch(Base):
    """Score d'une offre pour un CV. `assessment_key` invalide le cache si attentes/modèle changent."""

    __tablename__ = "job_matches"
    __table_args__ = (UniqueConstraint("cv_id", "job_id", name="uq_match_cv_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cv_id: Mapped[int] = mapped_column(ForeignKey("cv_profiles.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_offers.id", ondelete="CASCADE"), index=True)
    similarity: Mapped[float] = mapped_column(Float)
    llm_score: Mapped[float | None] = mapped_column(Float)
    final_score: Mapped[float] = mapped_column(Float, index=True)
    assessment: Mapped[dict | None] = mapped_column(JSONB)
    assessment_key: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    cv: Mapped[CVRecord] = relationship(back_populates="matches")
    job: Mapped[JobOffer] = relationship(back_populates="matches")


# --------------------------------------------------------------------------- #
# Sessions de recherche (interface web)
# --------------------------------------------------------------------------- #


class SearchSession(Base):
    """Une recherche lancée depuis l'interface : paramètres, avancement et résultats figés."""

    __tablename__ = "search_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    cv_id: Mapped[int | None] = mapped_column(ForeignKey("cv_profiles.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(255))
    params: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    step: Mapped[str | None] = mapped_column(String(50))
    error: Mapped[str | None] = mapped_column(Text)
    n_offers: Mapped[int | None] = mapped_column(Integer)
    results: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- #
# Avis de l'utilisateur sur les offres proposées
# --------------------------------------------------------------------------- #


class JobPin(Base):
    """Offre épinglée par un utilisateur (liste à garder sous la main, envoyable par e-mail)."""

    __tablename__ = "job_pins"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_pin_user_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_offers.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobFeedback(Base):
    """Avis d'un utilisateur sur une offre : « j'aime » (+1) ou « pas pour moi » (-1),
    motifs et commentaire libre.

    Réutilisé par les recherches suivantes : exclusion des offres écartées, ajustement du score des
    offres similaires (embeddings) et résumé transmis au LLM.
    """

    __tablename__ = "job_feedback"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_feedback_user_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_offers.id", ondelete="CASCADE"), index=True)
    search_id: Mapped[int | None] = mapped_column(ForeignKey("search_sessions.id", ondelete="SET NULL"))
    rating: Mapped[int] = mapped_column(Integer)  # +1 j'aime, -1 pas pour moi
    reasons: Mapped[list] = mapped_column(JSONB, default=list)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    job: Mapped[JobOffer] = relationship()


class MailImport(Base):
    """E-mail d'alerte déjà traité (identifié par l'empreinte de son Message-ID)."""

    __tablename__ = "mail_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    sender: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(500))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # imported, unknown_sender, unverified, not_alert, new_type, ignored_type
    status: Mapped[str] = mapped_column(String(30))
    mail_type: Mapped[str | None] = mapped_column(String(255))  # domaine de l'émetteur (free-work.com…)
    n_offers: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MailType(Base):
    """Type d'alerte e-mail reçu par un utilisateur (un par site émetteur : APEC, Free-Work…).

    Un type inconnu est créé « à valider » à sa première réception ; l'utilisateur l'active (ses offres
    sont alors importées, y compris celles des mails déjà reçus) ou l'ignore.
    """

    __tablename__ = "mail_types"
    __table_args__ = (UniqueConstraint("user_id", "domain", name="uq_mail_type_user_domain"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(255))
    label: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending, enabled, ignored
    sample_sender: Mapped[str | None] = mapped_column(String(255))
    sample_subject: Mapped[str | None] = mapped_column(String(500))
    n_mails: Mapped[int] = mapped_column(Integer, default=0)
    n_offers: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- #
# Supervision : mesures, journal d'audit, alertes
# --------------------------------------------------------------------------- #


class MetricEvent(Base):
    """Mesure technique : appel au LLM, requête à une source, relève des mails, sauvegarde…

    `kind` : llm, embedding, cache, source, http, mail_sync, backup ; `name` : étape ou source.
    """

    __tablename__ = "metric_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(60))
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer, default=1)
    detail: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    """Action d'administration (ou automatique) : qui, quoi, sur quoi."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor: Mapped[str] = mapped_column(String(255))  # email au moment de l'action, ou « système »
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[str | None] = mapped_column(Text)


class MonitorAlert(Base):
    """Alerte de supervision ouverte par `getiajob monitor` (une ligne par problème, clé stable)."""

    __tablename__ = "monitor_alerts"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    severity: Mapped[str] = mapped_column(String(10), default="warning")  # warning, critical
    message: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- #
# Suivi des candidatures
# --------------------------------------------------------------------------- #


class JobApplication(Base):
    """Candidature d'un utilisateur : offre (copie de ses informations), statut, prochaine étape.

    Les informations de l'offre sont recopiées pour que le suivi survive à la purge de l'offre.
    `job_id` est vide pour une candidature saisie à la main (offre trouvée ailleurs).
    """

    __tablename__ = "job_applications"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_application_user_job"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_offers.id", ondelete="SET NULL"), index=True, nullable=True
    )
    search_id: Mapped[int | None] = mapped_column(
        ForeignKey("search_sessions.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(500))
    company: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(1000))
    source: Mapped[str | None] = mapped_column(String(20))
    salary: Mapped[str | None] = mapped_column(String(255))
    score: Mapped[float | None] = mapped_column(
        Float
    )  # score final de l'offre pour l'un des CV de l'utilisateur
    summary: Mapped[str | None] = mapped_column(Text)  # synthèse de l'IA au moment de la candidature
    status: Mapped[str] = mapped_column(String(20), default="applied")
    applied_on: Mapped[date] = mapped_column(Date)
    next_step: Mapped[str | None] = mapped_column(String(255))
    next_step_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    notes: Mapped[list[ApplicationNote]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="(ApplicationNote.happened_on.desc(), ApplicationNote.id.desc())",
    )


class ApplicationNote(Base):
    """Étape ou commentaire du suivi d'une candidature (entretien, relance, note, changement de statut)."""

    __tablename__ = "application_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("job_applications.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30), default="note")
    happened_on: Mapped[date] = mapped_column(Date)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    application: Mapped[JobApplication] = relationship(back_populates="notes")
