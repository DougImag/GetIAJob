"""Configuration technique (variables d'environnement / .env) et préférences utilisateur (YAML)."""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Paramètres techniques, surchargeables par variables d'environnement (ex: LLM_MODEL)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://getiajob:getiajob@db:5432/getiajob"

    ollama_base_url: str = "http://ollama:11434"
    llm_model: str = "qwen2.5:7b"
    llm_temperature: float = 0.0
    llm_num_ctx: int = 8192
    llm_concurrency: int = 2  # appels LLM simultanés (analyse du CV) ; cf. OLLAMA_NUM_PARALLEL
    llm_max_tokens: int = 3072  # longueur max d'une réponse du LLM (borne un appel parti en boucle)
    llm_timeout: int = 900  # secondes max par appel au LLM (évite une analyse bloquée indéfiniment)
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768

    preferences_file: Path = Path("config/preferences.yaml")
    output_dir: Path = Path("data/output")

    linkedin_request_delay: float = 2.5
    # Sources officielles (facultatives) : actives dès que leurs clés sont renseignées.
    francetravail_client_id: str = ""
    francetravail_client_secret: str = ""
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_request_delay: float = 1.0
    # Boîte de réception des alertes e-mail (APEC…) transférées par les utilisateurs, lue en IMAP.
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    mail_lookback_days: int = 31
    mail_delete_processed: bool = True  # supprime (corbeille) les alertes importées après traitement
    log_level: str = "INFO"
    display_timezone: str = "Europe/Paris"  # affichage des dates dans l'administration

    # Supervision (getiajob monitor) et notifications e-mail aux administrateurs
    alerts_enabled: bool = True
    alert_emails: str = ""  # destinataires séparés par des virgules ; vide = comptes administrateurs
    alert_remind_hours: int = 24  # rappel d'une alerte toujours ouverte
    smtp_host: str = ""  # vide = déduit de IMAP_HOST (imap.gmail.com → smtp.gmail.com)
    smtp_port: int = 465
    smtp_user: str = ""  # vide = IMAP_USER
    smtp_password: str = ""  # vide = IMAP_PASSWORD
    stuck_search_minutes: int = 30
    mail_stale_minutes: int = 60
    source_failure_threshold: int = 3  # échecs consécutifs d'une source avant alerte
    search_failure_rate: float = 0.5  # sur 24 h, à partir de 4 recherches
    backup_stale_hours: int = 48  # seulement si une sauvegarde a déjà été enregistrée
    adzuna_daily_quota: int = 250  # quota indicatif de la clé gratuite
    metrics_retention_days: int = 90
    offer_retention_days: int = 90

    # Interface web
    allow_signup: bool = True  # False pour fermer la création de comptes sur une instance publique
    # Adhésion sur invitation : création de compte avec un code envoyé par un administrateur
    # (le tout premier compte, sur une base vide, n'en a pas besoin).
    invite_only: bool = True
    invitation_days: int = 14  # validité d'une invitation
    public_url: str = ""  # adresse publique de l'application (lien d'invitation dans les mails)
    session_days: int = 30  # durée de la connexion persistante (« rester connecté »)


@lru_cache
def get_settings() -> Settings:
    return Settings()


# --------------------------------------------------------------------------- #
# Préférences utilisateur (config/preferences.yaml)
# --------------------------------------------------------------------------- #

RemoteMode = Literal["on-site", "remote", "hybrid"]
ExperienceLevel = Literal["internship", "entry", "associate", "mid-senior", "director", "executive"]


class SearchPreferences(BaseModel):
    keywords: list[str] = Field(
        default_factory=list,
        description="Requêtes imposées. Vide = générées par l'agent à partir du CV.",
    )
    sources: list[str] = Field(
        default_factory=lambda: ["linkedin", "francetravail", "adzuna", "apec"],
        description="Sources interrogées (celles sans clé configurée sont ignorées).",
    )
    locations: list[str] = Field(default_factory=lambda: ["France"])
    remote: list[RemoteMode] = Field(default_factory=list)
    experience_levels: list[ExperienceLevel] = Field(default_factory=list)
    days: int = Field(default=7, ge=1, le=30)
    max_queries: int = Field(default=4, ge=1)
    max_offers_per_query: int = Field(default=50, ge=1)


class Expectations(BaseModel):
    desired_job: str = ""
    start_date: date | None = None
    description: str = ""
    min_salary: int | None = None
    contract_types: list[str] = Field(default_factory=list)
    must_have_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    exclude_companies: list[str] = Field(default_factory=list)
    feedback: str = ""  # résumé des avis du candidat sur des offres précédentes

    def to_prompt(self) -> str:
        lines = []
        if self.desired_job.strip():
            lines.append(f"Poste recherché : {self.desired_job.strip()}")
        if self.start_date:
            lines.append(f"Date de prise de poste souhaitée : {self.start_date:%d/%m/%Y}")
        if self.description.strip():
            lines.append(self.description.strip())
        if self.min_salary:
            lines.append(f"Salaire minimum souhaité : {self.min_salary} € brut annuel")
        if self.contract_types:
            lines.append(f"Types de contrat acceptés : {', '.join(self.contract_types)}")
        if self.must_have_keywords:
            lines.append(f"Indispensable : {', '.join(self.must_have_keywords)}")
        if self.feedback.strip():
            lines.append(f"Retours du candidat sur des offres déjà proposées :\n{self.feedback.strip()}")
        return "\n".join(lines) or "Aucune attente particulière précisée."


class RankingPreferences(BaseModel):
    prefilter_top_k: int = Field(default=30, ge=1, description="Offres envoyées au LLM après tri sémantique")
    top_n: int = Field(default=10, ge=1)
    llm_weight: float = Field(default=0.7, ge=0, le=1)
    # Avis de l'utilisateur (figés à la création de la recherche) : aucun appel LLM supplémentaire
    hidden_job_ids: list[int] = Field(default_factory=list, description="Offres écartées : exclues")
    liked_job_ids: list[int] = Field(default_factory=list, description="Offres aimées : bonus aux similaires")
    disliked_job_ids: list[int] = Field(
        default_factory=list, description="Offres écartées sur le fond : malus aux offres similaires"
    )
    feedback_points: float = Field(default=10, ge=0, le=30, description="Ajustement max du score (points)")


class Preferences(BaseModel):
    search: SearchPreferences = Field(default_factory=SearchPreferences)
    expectations: Expectations = Field(default_factory=Expectations)
    ranking: RankingPreferences = Field(default_factory=RankingPreferences)


def load_preferences(path: Path | None = None) -> Preferences:
    path = path or get_settings().preferences_file
    if not path.exists():
        return Preferences()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Preferences.model_validate(data)


def preferences_from_state(state: dict) -> Preferences:
    """Préférences passées dans l'état du graphe (interface web), sinon fichier YAML (CLI)."""
    if state.get("preferences"):
        return Preferences.model_validate(state["preferences"])
    return load_preferences()


def keyword_in(keyword: str, text: str) -> bool:
    """Recherche insensible à la casse, sur mot entier (« java » ne matche pas « javascript »)."""
    pattern = rf"(?<!\w){re.escape(keyword.lower())}(?!\w)"
    return re.search(pattern, text.lower()) is not None
