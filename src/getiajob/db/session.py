from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from getiajob.config import get_settings
from getiajob.db.models import Base

# --------------------------------------------------------------------------- #
# Mode lecture seule (consultation d'un compte par un administrateur)
# --------------------------------------------------------------------------- #

# Positionné pour l'exécution en cours (un thread Streamlit par exécution de page) : toute requête
# qui modifie la base est alors refusée, quel que soit le chemin de code qui la déclenche.
READ_ONLY: ContextVar[bool] = ContextVar("getiajob_read_only", default=False)
_WRITE_SQL = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE|ALTER|CREATE|DROP|TRUNCATE|GRANT|REVOKE|COPY)\b", re.IGNORECASE
)


class ReadOnlyError(PermissionError):
    """Écriture tentée en mode lecture seule."""

    def __init__(self) -> None:
        super().__init__("Action impossible : tu consultes ce compte en lecture seule.")


_CTE_WRITE = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE)\b", re.IGNORECASE)


def is_write(statement: str) -> bool:
    if _WRITE_SQL.match(statement):
        return True
    # Requête WITH … INSERT/UPDATE/DELETE (CTE modifiante)
    return statement.lstrip()[:4].upper() == "WITH" and bool(_CTE_WRITE.search(statement))


def _guard(conn, cursor, statement, parameters, context, executemany) -> None:
    if READ_ONLY.get() and is_write(statement):
        raise ReadOnlyError()


@lru_cache
def get_engine() -> Engine:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    event.listen(engine, "before_cursor_execute", _guard)
    return engine


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Mises à niveau idempotentes des bases créées avant la v0.2 (create_all n'ajoute pas de colonnes).
# À remplacer par Alembic quand le schéma évoluera davantage.
UPGRADES = [
    "ALTER TABLE cv_profiles ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE",
    "ALTER TABLE cv_profiles ADD COLUMN IF NOT EXISTS file_data BYTEA",
    "ALTER TABLE cv_profiles ALTER COLUMN profile DROP NOT NULL",
    # v0.1 : index unique sur file_hash seul → unicité par (utilisateur, fichier)
    "DROP INDEX IF EXISTS ix_cv_profiles_file_hash",
    "CREATE INDEX IF NOT EXISTS ix_cv_profiles_file_hash ON cv_profiles (file_hash)",
    "CREATE INDEX IF NOT EXISTS ix_cv_profiles_user_id ON cv_profiles (user_id)",
    # v0.4 : analyse du CV en tâche de fond, fraîcheur des annonces dans la fiche
    "ALTER TABLE cv_profiles ADD COLUMN IF NOT EXISTS analysis_status VARCHAR(20)",
    "ALTER TABLE cv_profiles ADD COLUMN IF NOT EXISTS analysis_error TEXT",
    "ALTER TABLE cv_profiles ADD COLUMN IF NOT EXISTS analysis_started_at TIMESTAMPTZ",
    "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS max_offer_age_days INTEGER DEFAULT 7",
    "UPDATE user_profiles SET max_offer_age_days = 7 WHERE max_offer_age_days IS NULL",
    "ALTER TABLE user_profiles DROP COLUMN IF EXISTS start_date",
    # v0.6 : rôle administrateur. À l'ajout de la colonne, tous les comptes déjà existants
    # deviennent administrateurs (une seule fois : la colonne n'est ajoutée qu'une fois).
    """
    DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'is_admin'
        ) THEN
            ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT false;
            UPDATE users SET is_admin = true;
        END IF;
    END $$;
    """,
    # v0.7 : plusieurs sources d'offres (LinkedIn, France Travail, Adzuna)
    "ALTER TABLE job_offers ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'linkedin'",
    "ALTER TABLE job_offers ADD COLUMN IF NOT EXISTS fingerprint VARCHAR(40)",
    "CREATE INDEX IF NOT EXISTS ix_job_offers_fingerprint ON job_offers (fingerprint)",
    "ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS sources JSONB",
    # v0.8 : offres privées reçues par alerte e-mail (APEC)
    "ALTER TABLE job_offers ADD COLUMN IF NOT EXISTS owner_user_id INTEGER "
    "REFERENCES users(id) ON DELETE CASCADE",
    "CREATE INDEX IF NOT EXISTS ix_job_offers_owner_user_id ON job_offers (owner_user_id)",
    """
    DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_cv_user_hash') THEN
            ALTER TABLE cv_profiles ADD CONSTRAINT uq_cv_user_hash UNIQUE (user_id, file_hash);
        END IF;
    END $$;
    """,
    # v0.10.1 : score et synthèse de l'IA conservés avec la candidature
    "ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS salary VARCHAR(255)",
    "ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS score DOUBLE PRECISION",
    "ALTER TABLE job_applications ADD COLUMN IF NOT EXISTS summary TEXT",
    # v0.12.2 : avancement détaillé de l'analyse du CV (étapes, estimation du temps restant)
    "ALTER TABLE cv_profiles ADD COLUMN IF NOT EXISTS analysis_progress JSONB",
]


def init_db() -> None:
    """Crée l'extension pgvector, les tables et applique les mises à niveau (idempotent)."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for statement in UPGRADES:
            conn.execute(text(statement))
    _backfill_fingerprints()


def _backfill_fingerprints() -> None:
    """Empreinte titre + entreprise des offres collectées avant la v0.7 (dédoublonnage entre sources)."""
    from getiajob.db.models import JobOffer
    from getiajob.scraping.base import fingerprint

    with session_scope() as session:
        rows = session.execute(
            select(JobOffer.id, JobOffer.title, JobOffer.company).where(
                JobOffer.fingerprint.is_(None), JobOffer.company.is_not(None)
            )
        ).all()
        for job_id, title, company in rows:
            if fp := fingerprint(title, company):
                session.execute(update(JobOffer).where(JobOffer.id == job_id).values(fingerprint=fp))
