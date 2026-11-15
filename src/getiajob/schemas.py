"""Sorties structurées attendues des LLM."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

CV_SCHEMA_VERSION = 2

Seniority = Literal["junior", "mid", "senior", "lead", "executive"]


# --------------------------------------------------------------------------- #
# Profil extrait du CV
# --------------------------------------------------------------------------- #


class Contact(BaseModel):
    email: str | None = None
    phone: str | None = Field(default=None, description="Téléphone")
    location: str | None = Field(default=None, description="Ville / adresse")
    linkedin: str | None = Field(default=None, description="URL ou identifiant LinkedIn")
    github: str | None = None
    website: str | None = Field(default=None, description="Site / portfolio")


# Type de contrat déduit de l'intitulé ou de l'employeur quand le CV ne le précise pas
CONTRACT_HINTS = (
    (re.compile(r"\b(stages?|stagiaire|intern|internship)\b", re.I), "Stage"),
    (re.compile(r"\b(alternan(ce|t|te)|apprenti(e|ssage)?)\b", re.I), "Alternance"),
    (re.compile(r"\b(free[- ]?lance|ind[ée]pendant|auto[- ]?entrepreneur|portage)\b", re.I), "Freelance"),
    (re.compile(r"\b(int[ée]rim(aire)?)\b", re.I), "Intérim"),
    (re.compile(r"\bcdd\b", re.I), "CDD"),
)
DEFAULT_CONTRACT = "CDI"


def infer_contract(title: str | None, company: str | None) -> str:
    """Contrat par défaut d'une expérience : indice dans l'intitulé / l'employeur, sinon CDI."""
    where = " ".join(x for x in (title, company) if x)
    return next((kind for pattern, kind in CONTRACT_HINTS if pattern.search(where)), DEFAULT_CONTRACT)


class Experience(BaseModel):
    title: str = Field(description="Intitulé du poste")
    company: str | None = Field(default=None, description="Entreprise / organisation")
    location: str | None = None
    start: str | None = Field(default=None, description="Début tel qu'écrit (ex. '2025', 'sept. 2023')")
    end: str | None = Field(default=None, description="Fin telle qu'écrite, 'présent' si en cours")
    duration: str | None = Field(default=None, description="Durée si indiquée (ex. '6 mois')")
    contract_type: str | None = Field(
        default=None, description="Stage, CDI, CDD, alternance, freelance… si indiqué"
    )
    description: str | None = Field(default=None, description="Mission principale en une phrase")
    achievements: list[str] = Field(default_factory=list, description="Réalisations, une par puce")
    technologies: list[str] = Field(default_factory=list, description="Outils et technologies cités")

    @model_validator(mode="after")
    def _default_contract(self) -> Experience:
        """Contrat non précisé dans le CV : CDI (sauf indice de stage, alternance, freelance…)."""
        if not (self.contract_type or "").strip():
            self.contract_type = infer_contract(self.title, self.company)
        return self

    @property
    def period(self) -> str:
        dates = " – ".join(d for d in (self.start, self.end) if d)
        return ", ".join(p for p in (dates, self.duration) if p)


class Education(BaseModel):
    degree: str = Field(description="Diplôme ou formation (ex. 'Diplôme d'ingénieur', 'Baccalauréat')")
    school: str | None = Field(default=None, description="École / université / lycée")
    field: str | None = Field(default=None, description="Spécialité / domaine")
    location: str | None = None
    start: str | None = None
    end: str | None = None
    details: list[str] = Field(default_factory=list, description="Mention, options, matières clés…")

    @property
    def period(self) -> str:
        return " – ".join(d for d in (self.start, self.end) if d)


class Project(BaseModel):
    name: str
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    url: str | None = None


class SkillGroup(BaseModel):
    category: str = Field(description="Catégorie (ex. 'Langages', 'IA / NLP', 'Data / BI', 'Outils')")
    items: list[str] = Field(default_factory=list)


class Language(BaseModel):
    language: str = Field(description="Langue parlée (français, anglais…), pas un langage informatique")
    level: str | None = Field(default=None, description="Niveau (natif, C1, courant…)")


class CVProfile(BaseModel):
    """Profil complet du candidat, découpé par sections."""

    schema_version: int = CV_SCHEMA_VERSION
    full_name: str | None = None
    headline: str = ""
    summary: str = ""
    contact: Contact = Field(default_factory=Contact)
    seniority: Seniority | None = None
    years_of_experience: float | None = None
    target_job_titles: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    experiences: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    # Date de la dernière correction manuelle (None = profil tel qu'extrait par l'IA)
    edited_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy(cls, data):
        """Lit les profils v1 (listes de chaînes) pour ne rien casser avant leur ré-analyse."""
        # Un profil v1 n'a pas de schema_version et possède toujours la clé « hard_skills ».
        if not isinstance(data, dict) or "schema_version" in data or "hard_skills" not in data:
            return data
        data = dict(data, schema_version=1)
        hard_skills = data.pop("hard_skills") or []
        if hard_skills:
            data["skills"] = [{"category": "Compétences", "items": hard_skills}]
        data["languages"] = [
            {"language": lang} if isinstance(lang, str) else lang for lang in data.get("languages", [])
        ]
        data["education"] = [{"degree": e} if isinstance(e, str) else e for e in data.get("education", [])]
        data["experiences"] = [
            {**e, "start": e.get("period"), "achievements": e.get("highlights", [])}
            for e in data.get("experiences", [])
        ]
        if data.get("locations") and not data.get("contact"):
            data["contact"] = {"location": ", ".join(data["locations"])}
        data["headline"] = data.get("headline") or ""
        data["summary"] = data.get("summary") or ""
        return data

    @property
    def is_outdated(self) -> bool:
        return self.schema_version < CV_SCHEMA_VERSION

    @property
    def hard_skills(self) -> list[str]:
        return [item for group in self.skills for item in group.items]

    def to_text(self) -> str:
        """Représentation compacte utilisée pour l'embedding et les prompts."""
        parts = [
            self.headline,
            self.summary,
            f"Séniorité : {self.seniority or '?'} ({self.years_of_experience or '?'} ans d'expérience)",
            f"Postes visés : {', '.join(self.target_job_titles)}",
        ]
        parts += [f"{g.category} : {', '.join(g.items)}" for g in self.skills]
        if self.soft_skills:
            parts.append(f"Qualités : {', '.join(self.soft_skills)}")
        if self.languages:
            parts.append(
                "Langues : "
                + ", ".join(
                    f"{lang.language} ({lang.level})" if lang.level else lang.language
                    for lang in self.languages
                )
            )
        if self.industries:
            parts.append(f"Secteurs : {', '.join(self.industries)}")
        parts.append("Expériences :")
        for e in self.experiences:
            parts.append(f"- {e.title} @ {e.company or '?'} ({e.period or '?'})")
            parts += [f"  • {a}" for a in e.achievements[:4]]
            if e.technologies:
                parts.append(f"  Technologies : {', '.join(e.technologies)}")
        parts.append("Formation :")
        parts += [f"- {ed.degree}, {ed.school or '?'} ({ed.period or '?'})" for ed in self.education]
        if self.projects:
            parts.append("Projets :")
            parts += [f"- {p.name} : {p.description or ''}" for p in self.projects]
        if self.certifications:
            parts.append(f"Certifications : {', '.join(self.certifications)}")
        return "\n".join(p for p in parts if p)


# --- Sorties partielles : un appel LLM par section (plus fiable qu'un seul gros schéma) ---


class IdentityExtraction(BaseModel):
    full_name: str | None = Field(default=None, description="Prénom et nom du candidat")
    headline: str = Field(default="", description="Titre professionnel tel qu'écrit en haut du CV")
    summary: str = Field(default="", description="Phrase d'accroche / présentation du CV, recopiée")
    contact: Contact = Field(default_factory=Contact)


class ExperienceExtraction(BaseModel):
    experiences: list[Experience] = Field(default_factory=list)


class EducationExtraction(BaseModel):
    education: list[Education] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)


class ProjectExtraction(BaseModel):
    projects: list[Project] = Field(default_factory=list)


class SkillsExtraction(BaseModel):
    skills: list[SkillGroup] = Field(default_factory=list, description="Compétences techniques par catégorie")
    soft_skills: list[str] = Field(default_factory=list, description="Qualités humaines / savoir-être")
    languages: list[Language] = Field(default_factory=list, description="Langues parlées et niveau")
    interests: list[str] = Field(default_factory=list, description="Loisirs / centres d'intérêt")


class ProfileSynthesis(BaseModel):
    headline: str = Field(default="", description="Titre professionnel en quelques mots")
    summary: str = Field(default="", description="Résumé du profil en 2 phrases")
    seniority: Seniority = Field(description="Niveau de séniorité")
    years_of_experience: float | None = Field(
        default=None, description="Années d'expérience professionnelle cumulées (stages inclus)"
    )
    target_job_titles: list[str] = Field(
        default_factory=list, description="3 à 6 intitulés de poste réalistes à rechercher sur LinkedIn"
    )
    industries: list[str] = Field(default_factory=list, description="Secteurs d'activité")


# --------------------------------------------------------------------------- #
# Autres sorties
# --------------------------------------------------------------------------- #


class SearchPlan(BaseModel):
    queries: list[str] = Field(
        description="Requêtes de recherche LinkedIn courtes (2 à 4 mots), de la plus à la moins pertinente"
    )


class JobAssessment(BaseModel):
    """Évaluation d'une offre par l'agent 3."""

    score: int = Field(ge=0, le=100, description="Adéquation globale profil/attentes, de 0 à 100")
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    pros: list[str] = Field(default_factory=list, description="Points forts de l'offre pour ce candidat")
    cons: list[str] = Field(default_factory=list, description="Points de vigilance")
    summary: str = Field(description="Verdict en une ou deux phrases")
