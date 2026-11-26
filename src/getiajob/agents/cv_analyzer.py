"""Agent 1 — Analyse du CV.

1. Extraction du texte en respectant la mise en page (colonnes, titres de sections) : `cv_text`.
2. Une extraction LLM ciblée par section (identité, expériences, formation, projets, compétences),
   en parallèle : de petits schémas sont bien mieux remplis par un modèle 7B qu'un schéma géant.
3. Une synthèse (séniorité, postes cibles, secteurs) à partir des sections structurées.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import delete, select

from getiajob.config import get_settings
from getiajob.cv_text import extract_cv_text
from getiajob.db import CVRecord, JobMatch, session_scope
from getiajob.llm import get_embeddings, structured
from getiajob.schemas import (
    CVProfile,
    EducationExtraction,
    ExperienceExtraction,
    IdentityExtraction,
    ProfileSynthesis,
    ProjectExtraction,
    SkillsExtraction,
)
from getiajob.services.monitoring import timed

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = ("pdf", "docx", "md", "txt")

COMMON_RULES = """Tu analyses le texte d'un CV extrait d'un PDF ou d'un document Word.
- Les titres de sections détectés commencent par « ## ».
- Le CV peut être sur plusieurs colonnes : une information peut être éloignée de son titre.
- Recopie fidèlement (même langue, mêmes termes), sans rien inventer ni reformuler.
- Si une information est absente, laisse le champ vide ou la liste vide : n'écris jamais
  « Non spécifié » et ne calcule jamais une date qui n'est pas écrite."""

TASKS: dict[str, tuple[type[BaseModel], str]] = {
    "identity": (
        IdentityExtraction,
        "Extrais l'identité du candidat : nom complet, titre professionnel (en tête du CV), phrase "
        "d'accroche/présentation si présente, et coordonnées (email, téléphone, ville, LinkedIn, "
        "GitHub, site).",
    ),
    "experiences": (
        ExperienceExtraction,
        "Extrais TOUTES les expériences professionnelles : emplois, stages, alternances, missions, "
        "jobs étudiants. N'inclus PAS les formations, diplômes, classes préparatoires ni les projets "
        "académiques ou personnels. Pour chacune : intitulé du poste, entreprise, lieu, dates de "
        "début/fin et durée telles qu'écrites, type de contrat (stage, CDI…) si indiqué, la mission "
        "en une phrase, chaque réalisation (une par puce) et les technologies citées (y compris une "
        "ligne « Stack technique »). De la plus récente à la plus ancienne.",
    ),
    "education": (
        EducationExtraction,
        "Extrais TOUTES les formations et diplômes (école d'ingénieur, université, classe "
        "préparatoire, lycée/baccalauréat, formations diplômantes) : diplôme, établissement, "
        "spécialité, lieu, dates, détails (mention, options). De la plus récente à la plus ancienne. "
        "Extrais aussi les certifications (liste à part).",
    ),
    "projects": (
        ProjectExtraction,
        "Extrais les projets (académiques, personnels, open source) : nom, description en une ou "
        "deux phrases, technologies utilisées, lien éventuel. N'inclus pas les expériences en "
        "entreprise.",
    ),
    "skills": (
        SkillsExtraction,
        "Extrais :\n"
        "- skills : les compétences techniques, regroupées par catégorie comme sur le CV (ex. "
        "« Langages », « IA/NLP », « Data/BI », « Outils ») ; si le CV ne les regroupe pas, crée "
        "des catégories pertinentes ;\n"
        "- soft_skills : les qualités humaines (dynamique, travail en équipe…) ;\n"
        "- languages : les langues parlées (français, anglais…) avec leur niveau — ne pas confondre "
        "avec les langages de programmation ;\n"
        "- interests : les loisirs / centres d'intérêt / hobbies.",
    ),
}

SYNTHESIS_PROMPT = """Tu es un expert en recrutement. À partir du profil structuré d'un candidat :
- headline : son titre professionnel en quelques mots (garde celui du CV s'il existe) ;
- summary : résumé du profil en 2 phrases ;
- seniority : junior (< 2 ans ou étudiant), mid (2-5 ans), senior (5-10 ans), lead, executive ;
- years_of_experience : années d'expérience professionnelle cumulées, calculées uniquement à
  partir des expériences (stages inclus au prorata de leur durée) ; les études, la classe
  préparatoire et les projets ne comptent pas ;
- target_job_titles : 3 à 6 intitulés de poste réalistes et recherchés sur LinkedIn, cohérents
  avec la formation, les expériences et la séniorité ;
- industries : secteurs d'activité où il a travaillé.
Réponds dans la langue du CV."""


# Étapes de l'analyse, dans l'ordre (les 5 extractions tournent en parallèle, puis la synthèse et
# l'indexation). Clé → (libellé, mesure de durée enregistrée par `timed`, pour estimer le temps restant).
STEPS: dict[str, tuple[str, tuple[str, str]]] = {
    "identity": ("Identité", ("llm", "cv_identity")),
    "experiences": ("Expériences", ("llm", "cv_experiences")),
    "education": ("Formation", ("llm", "cv_education")),
    "projects": ("Projets", ("llm", "cv_projects")),
    "skills": ("Compétences", ("llm", "cv_skills")),
    "synthesis": ("Synthèse du profil", ("llm", "cv_synthese")),
    "embedding": ("Indexation", ("embedding", "cv")),
}
PARALLEL_STEPS = tuple(TASKS)

# Rappel d'avancement : progress(étape, "running" | "done" | "failed")
Progress = Callable[[str, str], None]


def _noop(step: str, state: str) -> None:
    pass


def extract_text(data: bytes, file_name: str) -> str:
    return extract_cv_text(data, file_name)


def _run_task(name: str, text: str, progress: Progress = _noop) -> BaseModel | Exception:
    # La consigne vient AVANT le CV : placer le CV en tête (pour le cache de prompt d'Ollama) fait perdre
    # des expériences et les dates (mesuré : 7 expériences sans dates au lieu de 12 avec qwen2.5:7b).
    schema, instruction = TASKS[name]
    llm = structured(schema)
    progress(name, "running")
    try:
        with timed("llm", f"cv_{name}"):
            result = llm.invoke(
                [("system", f"{COMMON_RULES}\n\n{instruction}"), ("human", f"Texte du CV :\n\n{text}")]
            )
    except Exception as exc:  # une section ratée ne doit pas faire échouer toute l'analyse
        log.warning("Extraction « %s » impossible : %s", name, exc)
        progress(name, "failed")
        return exc
    progress(name, "done")
    return result


def _dedupe(items: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for item in items:
        item = item.strip(" •-–\t")
        if item and item.lower() not in seen:
            seen[item.lower()] = item
    return list(seen.values())


# --------------------------------------------------------------------------- #
# Garde-fous déterministes contre les approximations du LLM
# --------------------------------------------------------------------------- #

PLACEHOLDERS = {
    "",
    "-",
    "?",
    "n/a",
    "na",
    "none",
    "null",
    "inconnu",
    "non spécifié",
    "non specifie",
    "non précisé",
    "non precise",
    "non renseigné",
    "non indiqué",
    "aucun",
    "aucune",
}
OPTIONAL_FIELDS = {
    "full_name",
    "company",
    "location",
    "start",
    "end",
    "duration",
    "contract_type",
    "description",
    "school",
    "field",
    "url",
    "level",
    "email",
    "phone",
    "linkedin",
    "github",
    "website",
}
DATE_FIELDS = {"start", "end", "duration"}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?:\+\d{2,3}[\s.]?|0)[1-9](?:[\s.-]?\d{2}){4}")
LINKEDIN_RE = re.compile(r"(?:linkedin\.com/in/|(?<![\w/])in/)[\w-]+", re.IGNORECASE)
GITHUB_RE = re.compile(r"github\.com/[\w-]+", re.IGNORECASE)
CITY_RE = re.compile(r"\b\d{5}\s+[A-ZÀ-Ÿ][\wÀ-ÿ' -]{1,40}")


def _norm(value: str) -> str:
    value = value.replace("’", "'").replace("`", "'")
    return re.sub(r"\s+", " ", value).strip().lower()


def _grounded(item: str, text_norm: str) -> bool:
    """L'élément (ou chacun de ses mots significatifs) figure-t-il dans le CV ?"""
    norm = _norm(item)
    if norm in text_norm:
        return True
    words = [w for w in re.findall(r"[\w+#./-]+", norm) if len(w) > 2]
    return bool(words) and all(w in text_norm for w in words)


def _ground_lists(profile: CVProfile, text_norm: str) -> None:
    """Retire les compétences, qualités, intérêts et langues absents du texte (hallucinations)."""
    keep = lambda items: [i for i in items if _grounded(i, text_norm)]  # noqa: E731
    profile.soft_skills = keep(profile.soft_skills)
    profile.interests = keep(profile.interests)
    profile.languages = [lang for lang in profile.languages if _grounded(lang.language, text_norm)]
    soft = {_norm(s) for s in profile.soft_skills}
    groups = []
    for group in profile.skills:
        items = [i for i in keep(group.items) if _norm(i) not in soft]
        if items:
            groups.append(group.model_copy(update={"items": items}))
    profile.skills = groups
    for exp in profile.experiences:
        exp.technologies = keep(exp.technologies)
    for project in profile.projects:
        project.technologies = keep(project.technologies)


def _clean(data, text_norm: str, key: str | None = None):
    """Supprime les valeurs bidon et les dates absentes du texte du CV (dates « calculées »)."""
    if isinstance(data, dict):
        return {k: _clean(v, text_norm, k) for k, v in data.items()}
    if isinstance(data, list):
        cleaned = [_clean(v, text_norm) for v in data]
        return [v for v in cleaned if v is not None and not (isinstance(v, str) and not v.strip())]
    if isinstance(data, str):
        value = data.strip()
        if key in OPTIONAL_FIELDS and value.lower() in PLACEHOLDERS:
            return None
        if key in DATE_FIELDS and _norm(value) not in text_norm:
            return None
        return value
    return data


def _contact_fallback(profile: CVProfile, text: str) -> None:
    """Coordonnées repérées par expressions régulières : complète ou corrige le LLM."""
    c = profile.contact
    emails = EMAIL_RE.findall(text)
    if emails and (not c.email or c.email not in text):
        c.email = emails[0]
    if not c.phone and (m := PHONE_RE.search(text)):
        c.phone = m.group(0).strip()
    if not c.linkedin and (m := LINKEDIN_RE.search(text)):
        c.linkedin = m.group(0)
    if not c.github and (m := GITHUB_RE.search(text)):
        c.github = m.group(0)
    if not c.location and (m := CITY_RE.search(text)):
        c.location = m.group(0).strip()


def extract_profile(text: str, progress: Progress = _noop) -> CVProfile:
    """Texte balisé du CV → profil structuré (sans accès à la base)."""
    workers = max(1, get_settings().llm_concurrency)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Chaque tâche reçoit une copie du contexte : utilisateur à qui imputer les tokens (`usage`)
        futures = {
            name: pool.submit(contextvars.copy_context().run, _run_task, name, text, progress)
            for name in TASKS
        }
        results = {name: future.result() for name, future in futures.items()}
    errors = [r for r in results.values() if isinstance(r, Exception)]
    if len(errors) == len(results):
        cause = f"{type(errors[0]).__name__} : {errors[0]}"[:300]
        raise RuntimeError(f"Le LLM n'a pu extraire aucune section du CV ({cause}).")
    parts = {name: None if isinstance(r, Exception) else r for name, r in results.items()}

    identity = parts["identity"] or IdentityExtraction()
    experiences = parts["experiences"] or ExperienceExtraction()
    education = parts["education"] or EducationExtraction()
    projects = parts["projects"] or ProjectExtraction()
    skills = parts["skills"] or SkillsExtraction()

    profile = CVProfile(
        full_name=identity.full_name,
        headline=identity.headline.strip(),
        summary=identity.summary.strip(),
        contact=identity.contact,
        experiences=[e for e in experiences.experiences if e.title.strip()],
        education=[e for e in education.education if e.degree.strip()],
        certifications=_dedupe(education.certifications),
        projects=[p for p in projects.projects if p.name.strip()],
        skills=[g.model_copy(update={"items": _dedupe(g.items)}) for g in skills.skills if g.items],
        soft_skills=_dedupe(skills.soft_skills),
        languages=[lang for lang in skills.languages if lang.language.strip()],
        interests=_dedupe(skills.interests),
    )

    text_norm = _norm(text)
    profile = CVProfile.model_validate(_clean(profile.model_dump(mode="json"), text_norm))
    _ground_lists(profile, text_norm)
    _contact_fallback(profile, text)

    sections = profile.model_dump(
        include={"headline", "summary", "experiences", "education", "projects", "skills", "languages"}
    )
    progress("synthesis", "running")
    try:
        with timed("llm", "cv_synthese"):
            synthesis: ProfileSynthesis = structured(ProfileSynthesis).invoke(
                [
                    ("system", SYNTHESIS_PROMPT),
                    ("human", json.dumps(sections, ensure_ascii=False, indent=1)),
                ]
            )
    except Exception as exc:
        log.warning("Synthèse du profil impossible : %s", exc)
        progress("synthesis", "failed")
    else:
        progress("synthesis", "done")
        profile.headline = profile.headline or synthesis.headline.strip()
        profile.summary = profile.summary or synthesis.summary.strip()
        profile.seniority = synthesis.seniority
        profile.years_of_experience = synthesis.years_of_experience
        profile.target_job_titles = _dedupe(synthesis.target_job_titles)[:6]
        profile.industries = _dedupe(synthesis.industries)
    return profile


def save_cv(data: bytes, file_name: str, user_id: int | None = None) -> int:
    """Enregistre le fichier et son texte (sans appel au LLM). Renvoie l'id, existant si doublon."""
    file_hash = hashlib.sha256(data).hexdigest()
    with session_scope() as session:
        owner = CVRecord.user_id == user_id if user_id is not None else CVRecord.user_id.is_(None)
        record = session.scalar(select(CVRecord).where(owner, CVRecord.file_hash == file_hash))
        if record:
            if record.file_data is None:  # CV importé avant la v0.2 : on conserve le fichier
                record.file_data = data
            return record.id

        text = extract_text(data, file_name)
        if not text.strip():
            raise ValueError(
                "Aucun texte extrait du CV (PDF scanné ?). Fournis une version texte, .docx ou .md."
            )
        record = CVRecord(
            user_id=user_id, file_name=file_name, file_hash=file_hash, file_data=data, raw_text=text
        )
        session.add(record)
        session.flush()
        return record.id


def ensure_profile(cv_id: int, *, force: bool = False, progress: Progress = _noop) -> CVProfile:
    """Renvoie le profil du CV ; l'(ré)extrait s'il manque, s'il est obsolète ou si force.

    Aucune connexion à la base n'est gardée pendant les appels au LLM (plusieurs minutes sans GPU) :
    lecture du CV, puis extraction hors transaction, puis enregistrement dans une nouvelle session.
    """
    with session_scope() as session:
        record = session.get(CVRecord, cv_id)
        if record is None:
            raise ValueError(f"CV {cv_id} introuvable")
        if record.profile and not force:
            current = CVProfile.model_validate(record.profile)
            if not current.is_outdated:
                log.info("CV déjà analysé (id=%s), réutilisation du profil.", cv_id)
                return current
            log.info("Profil du CV %s obsolète (schéma v%s) : ré-analyse.", cv_id, current.schema_version)
        file_name = record.file_name
        # Le texte est ré-extrait avec la version courante du parseur
        raw_text = extract_text(record.file_data, file_name) if record.file_data else record.raw_text

    log.info("Analyse du CV %s avec le LLM (par sections)…", file_name)
    profile = extract_profile(raw_text, progress)
    progress("embedding", "running")
    with timed("embedding", "cv"):
        embedding = get_embeddings().embed_query(profile.to_text())
    progress("embedding", "done")

    with session_scope() as session:
        record = session.get(CVRecord, cv_id)
        if record is None:  # supprimé pendant l'analyse
            raise ValueError(f"CV {cv_id} supprimé pendant l'analyse")
        record.raw_text = raw_text
        record.profile = profile.model_dump(mode="json")
        record.embedding = embedding
        # Les évaluations d'offres reposaient sur l'ancien profil : elles seront recalculées.
        session.execute(delete(JobMatch).where(JobMatch.cv_id == cv_id))
    log.info(
        "Profil extrait : %s — %d expériences, %d formations, %d projets, %d compétences",
        profile.headline,
        len(profile.experiences),
        len(profile.education),
        len(profile.projects),
        len(profile.hard_skills),
    )
    return profile


def analyze_cv(path: Path, *, force: bool = False) -> tuple[int, CVProfile]:
    """Usage CLI : enregistre et analyse un CV à partir d'un fichier local."""
    if not path.exists():
        raise FileNotFoundError(f"CV introuvable : {path}")
    cv_id = save_cv(path.read_bytes(), path.name)
    return cv_id, ensure_profile(cv_id, force=force)


def cv_analyzer_node(state: dict) -> dict:
    force = state.get("force_cv", False)
    if state.get("cv_id"):
        cv_id = state["cv_id"]
        profile = ensure_profile(cv_id, force=force)
    else:
        cv_id, profile = analyze_cv(Path(state["cv_path"]), force=force)
    return {"cv_id": cv_id, "profile": profile.model_dump(mode="json")}
