"""Niveau d'expérience exigé par une offre, comparé au niveau visé par le candidat.

Le filtre « niveau d'expérience » de LinkedIn est peu fiable (beaucoup d'offres « Senior »,
« Lead » ou « 5 ans d'expérience » remontent avec « Débutant ») et Adzuna n'en a pas : on relit
donc le titre, les critères de la source et la description pour écarter les offres trop senior.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

# Années d'expérience au plus acceptables pour chaque niveau visé (None = pas de limite).
LEVEL_MAX_YEARS: dict[str, float | None] = {
    "internship": 1,
    "entry": 2,
    "associate": 4,
    "mid-senior": None,
    "director": None,
    "executive": None,
}
LEVEL_PROMPT = {
    "internship": "stage / alternance",
    "entry": "débutant (moins de 2 ans d'expérience)",
    "associate": "junior (2 à 4 ans d'expérience)",
    "mid-senior": "confirmé / senior",
    "director": "directeur",
    "executive": "dirigeant",
}

SENIOR_TITLE = re.compile(
    r"\b(senior|sr|lead|tech ?lead|leader|expert|experte|confirme|confirmee|experimente|experimentee|"
    r"principal|staff|head|directeur|directrice|director|vp|chief|cto|cdo|architecte?|"
    r"(?:engineering|engineer|tech|technical|team|it|data|ia|ai) manager)\b"
)
JUNIOR_TITLE = re.compile(
    r"\b(junior|jr|debutant|debutante|jeune diplome|jeune diplomee|graduate|entry level|premier emploi)\b"
)
INTERN_TITLE = re.compile(
    r"\b(stage|stagiaire|intern|internship|alternance|alternant|alternante|apprenti|apprentie)\b"
)
JUNIOR_TEXT = re.compile(
    r"\b(debutant(e)?s? accepte|jeunes? diplome|premier emploi|"
    r"sortie? d.ecole|entry level|new grad|graduate program)"
)

# « Niveau hiérarchique » (LinkedIn) ou « Expérience » (France Travail).
CRITERIA_SENIOR = {"cadre superieur", "directeur", "direction"}
CRITERIA_MID = {"confirme"}
CRITERIA_JUNIOR = {"premier emploi", "debutant accepte", "stagiaire / alternant"}

_NUM = r"(\d{1,2})"
YEARS = re.compile(
    rf"(?:au moins|minimum|min\.?|plus de|>)?\s*{_NUM}\s*(?:\+|ans? ?\+|(?:a|-|/)\s*\d{{1,2}})?\s*"
    r"(?:\+\s*)?(?:ans|an\(s\)|an|annees|years?|yrs)\b"
)
# Ce qui suit doit parler d'expérience (et non de l'âge de l'entreprise).
EXPERIENCE_AFTER = re.compile(
    r"^\W{0,3}(?:minimum|min|ou plus|et plus|requis|d.experience|d.exp|of experience|experience|"
    r"en (?:tant|cdi|developpement|ingenierie|data|ia|ml|machine|software|python|conseil|production|mlops)|"
    r"de (?:developpement|dev|code|pratique|programmation|conception)|"
    r"as an?|in (?:software|data|ml|machine|a similar)|dans (?:un|une|des|le|la) |sur (?:un|des) |"
    r"hands-on|d.anciennete|professional)"
)
NOT_EXPERIENCE_BEFORE = re.compile(
    r"(depuis|il y a|fort de|forte de|fondee?|creee?|cree en|existe|up to|pendant|for up|age|"
    r"plus de \d+ ans d.expertise|historique|anniversaire)\W*(?:plus de\s*)?$"
)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.replace("’", "'")


def required_years(text: str | None) -> float | None:
    """Années d'expérience minimales demandées (« 3 ans d'expérience », « 5+ years »…), sinon None."""
    if not text:
        return None
    norm = _norm(text)
    found: list[int] = []
    for m in YEARS.finditer(norm):
        years = int(m.group(1))
        if not 1 <= years <= 20:
            continue
        before = norm[max(0, m.start() - 30) : m.start()]
        after = norm[m.end() : m.end() + 40]
        if NOT_EXPERIENCE_BEFORE.search(before) or not EXPERIENCE_AFTER.search(after):
            continue
        found.append(years)
    return float(max(found)) if found else None


@dataclass
class OfferLevel:
    years: float | None = None  # expérience minimale demandée
    senior_title: bool = False
    junior: bool = False  # offre explicitement ouverte aux débutants
    internship: bool = False
    criteria: str = ""  # « mid », « senior », « junior » ou vide


def offer_level(title: str, description: str | None, criteria: dict | None) -> OfferLevel:
    t = _norm(title or "")
    level = OfferLevel(
        years=required_years(description),
        senior_title=bool(SENIOR_TITLE.search(t)),
        junior=bool(JUNIOR_TITLE.search(t) or JUNIOR_TEXT.search(_norm(description or ""))),
        internship=bool(INTERN_TITLE.search(t)),
    )
    for key in ("Niveau hiérarchique", "Expérience"):
        value = _norm(str((criteria or {}).get(key, "")))
        if value in CRITERIA_SENIOR:
            level.criteria = "senior"
        elif value in CRITERIA_MID:
            level.criteria = "mid"
        elif value in CRITERIA_JUNIOR or value.startswith("debutant"):
            level.criteria = "junior"
            level.junior = True
        # France Travail : « 3 An(s) », « Expérience exigée de 5 An(s) »
        elif m := re.search(r"(\d+) an", value):
            level.years = max(level.years or 0, float(m.group(1)))
    if JUNIOR_TITLE.search(t):
        level.senior_title = False  # « Junior / Senior » : l'offre reste ouverte aux juniors
    return level


def max_years(levels: Iterable[str]) -> float | None:
    """Expérience maximale acceptable pour les niveaux visés ; None = pas de contrainte."""
    levels = list(levels)
    if not levels:
        return None
    limits = [LEVEL_MAX_YEARS.get(lv) for lv in levels]
    return None if any(v is None for v in limits) else max(limits)


def level_prompt(levels: Iterable[str]) -> str:
    return ", ".join(LEVEL_PROMPT.get(lv, lv) for lv in levels)


# Points de bonus / malus sur le score final.
JUNIOR_BONUS = 5.0
MID_PENALTY = -8.0
NEAR_PENALTY = -12.0  # un an de plus que le niveau visé (« 3 ans » pour un débutant)


def level_check(
    title: str, description: str | None, criteria: dict | None, levels: Iterable[str]
) -> tuple[str | None, float]:
    """(raison d'exclusion, ajustement en points) de l'offre pour les niveaux visés."""
    levels = list(levels)
    limit = max_years(levels)
    if limit is None:
        return None, 0.0
    lv = offer_level(title, description, criteria)
    if lv.internship and "internship" not in levels:
        return "stage / alternance", 0.0
    if lv.senior_title:
        return f"niveau trop senior (titre : {title})", 0.0
    if lv.criteria == "senior":
        return "niveau trop senior (niveau indiqué par la source)", 0.0
    too_many = lv.years is not None and lv.years > limit
    if lv.junior:
        # Offre affichée « débutant / premier emploi » : on la garde même si elle demande des années.
        return None, 0.0 if too_many else JUNIOR_BONUS
    if too_many and lv.years > limit + 1:
        return f"niveau trop senior ({lv.years:g} ans d'expérience demandés)", 0.0
    if too_many:
        return None, NEAR_PENALTY
    if lv.criteria == "mid" and lv.years is None:
        # « Confirmé » (LinkedIn) est souvent mis par défaut : sans effet si l'offre précise ses années.
        return None, MID_PENALTY
    return None, 0.0
