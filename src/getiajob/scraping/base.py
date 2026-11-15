"""Socle commun aux sources d'offres (LinkedIn, France Travail, Adzuna)."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

SOURCE_LABELS = {
    "linkedin": "LinkedIn",
    "francetravail": "France Travail",
    "adzuna": "Adzuna",
    "apec": "APEC",
}
ALL_SOURCES = list(SOURCE_LABELS)


@dataclass(frozen=True)
class SourceInfo:
    """Fiche descriptive d'une source (page Admin › Sources d'annonces)."""

    kind: str
    site: str
    description: str
    env_vars: tuple[str, ...] = ()
    signup_url: str | None = None


SOURCE_INFO = {
    "linkedin": SourceInfo(
        kind="Scraping des pages publiques",
        site="https://www.linkedin.com/jobs",
        description="Offres LinkedIn sans compte, avec filtres télétravail et niveau. Détail récupéré "
        "offre par offre (lent, délais aléatoires). Contraire aux CGU : usage personnel.",
        env_vars=("LINKEDIN_REQUEST_DELAY",),
    ),
    "francetravail": SourceInfo(
        kind="API officielle",
        site="https://candidat.francetravail.fr/offres/emploi",
        description="API « Offres d'emploi v2 » : description complète, contrat, expérience, salaire, "
        "compétences, offres de sites partenaires. ⚠ Retirée du catalogue en libre-service de "
        "francetravail.io (fin 2026) : accès restreint, sur demande via le formulaire de contact.",
        env_vars=("FRANCETRAVAIL_CLIENT_ID", "FRANCETRAVAIL_CLIENT_SECRET"),
        signup_url="https://francetravail.io",
    ),
    "adzuna": SourceInfo(
        kind="API officielle (agrégateur)",
        site="https://www.adzuna.fr",
        description="Agrège de nombreux sites d'emploi et cabinets. Salaire réel ou estimé ; "
        "description limitée à un extrait. Quota gratuit journalier.",
        env_vars=("ADZUNA_APP_ID", "ADZUNA_APP_KEY"),
        signup_url="https://developer.adzuna.com",
    ),
    "apec": SourceInfo(
        kind="Alertes e-mail (IMAP)",
        site="https://www.apec.fr",
        description="Alertes APEC transférées par chaque utilisateur vers la boîte de GetIAJob. Les offres "
        "sont rattachées à l'expéditeur (adresse de son compte) et visibles de lui seul. Intitulé, "
        "entreprise, contrat et lieu ; pas de description complète.",
        env_vars=("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"),
    ),
}
# Préfixe de l'identifiant stocké en base (colonne historique `linkedin_id`). LinkedIn reste
# sans préfixe pour ne pas invalider les offres déjà collectées.
ID_PREFIX = {"linkedin": "", "francetravail": "ft:", "adzuna": "adz:", "apec": "apec:"}


@dataclass
class JobPosting:
    """Offre normalisée, quelle que soit sa source."""

    source: str
    source_id: str
    title: str
    url: str
    company: str | None = None
    location: str | None = None
    posted_at: datetime | None = None
    description: str | None = None
    salary: str | None = None
    criteria: dict[str, str] = field(default_factory=dict)

    @property
    def external_id(self) -> str:
        return f"{ID_PREFIX.get(self.source, self.source + ':')}{self.source_id}"


def configured_sources() -> list[str]:
    """Sources utilisables sur cette instance : France Travail et Adzuna exigent leurs clés d'API."""
    from getiajob.config import get_settings

    s = get_settings()
    ok = {
        "linkedin": True,
        "francetravail": bool(s.francetravail_client_id and s.francetravail_client_secret),
        "adzuna": bool(s.adzuna_app_id and s.adzuna_app_key),
        "apec": bool(s.imap_host and s.imap_user and s.imap_password),
    }
    return [name for name in ALL_SOURCES if ok[name]]


class JobSource(Protocol):
    name: str

    def search(
        self, keywords: str, location: str, *, days: int, limit: int, **filters
    ) -> Iterator[JobPosting]:
        """Offres correspondant à la requête (description éventuellement absente)."""
        ...

    def details(self, posting: JobPosting) -> JobPosting:
        """Complète la description si la recherche ne la fournit pas (sinon renvoie l'offre telle quelle)."""
        ...

    def close(self) -> None: ...


TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<\s*(br|/p|/li|/div)\s*/?>", re.IGNORECASE)


def clean_html(value: str | None) -> str | None:
    """Texte brut à partir d'un fragment HTML (titres et descriptions des API)."""
    if not value:
        return None
    text = html.unescape(TAG_RE.sub("", BR_RE.sub("\n", value)))
    text = "\n".join(" ".join(line.replace("\xa0", " ").split()) for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def normalize(value: str | None) -> str:
    """Minuscules, sans accents ni ponctuation : sert à comparer lieux, titres et entreprises."""
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


# Mentions qui varient d'un site à l'autre pour une même annonce.
_NOISE_RE = re.compile(r"\b(h f|f h|m f|f m|h f x|cdi|cdd|freelance|stage|alternance)\b")
_COMPANY_NOISE_RE = re.compile(r"\b(sas|sasu|sa|sarl|groupe|group|france)\b")


def fingerprint(title: str, company: str | None) -> str | None:
    """Empreinte titre + entreprise pour repérer une même offre publiée sur plusieurs sites.

    None si l'entreprise est inconnue (trop de faux doublons sinon).
    """
    comp = " ".join(_COMPANY_NOISE_RE.sub(" ", normalize(company)).split())
    if not comp:
        return None
    tit = " ".join(_NOISE_RE.sub(" ", normalize(title)).split())
    return hashlib.sha1(f"{tit}|{comp}".encode()).hexdigest()
