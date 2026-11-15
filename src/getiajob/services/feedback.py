"""Avis de l'utilisateur sur les offres proposées, réutilisés par les recherches suivantes.

Trois usages, du moins au plus coûteux :
1. **Exclusion** : une offre écartée ne revient plus (gratuit, et économise une évaluation LLM).
2. **Ajustement sémantique** : les offres proches (embeddings déjà calculés) d'une offre aimée
   gagnent des points, celles proches d'une offre écartée « sur le fond » en perdent. Appliqué dès
   la pré-sélection, il change aussi les offres envoyées au LLM. Aucun appel LLM.
3. **Résumé pour le LLM** : quelques lignes (motifs, commentaires) ajoutées aux attentes, lues
   par l'agent qui génère les requêtes et par celui qui note les offres. Coût : quelques centaines
   de tokens par évaluation, et ré-évaluation des offres déjà notées quand les avis changent.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, select

from getiajob.db import JobFeedback, JobOffer, UserProfile, session_scope

LIKE, DISLIKE = 1, -1

# Motifs de refus proposés dans l'interface
REASONS = {
    "role": "Poste / missions hors cible",
    "too_junior": "Niveau trop junior",
    "too_senior": "Niveau trop senior",
    "tech": "Technologies / compétences",
    "sector": "Secteur d'activité",
    "company": "Entreprise",
    "location": "Localisation / trajet",
    "remote": "Télétravail insuffisant",
    "salary": "Salaire",
    "contract": "Type de contrat",
    "seen": "Déjà vue / déjà postulé",
}
# Motifs qui tiennent au contenu de l'offre : les offres similaires sont pénalisées.
# Les autres (entreprise, salaire, lieu, déjà vue…) ne disent rien des offres « qui lui ressemblent ».
CONTENT_REASONS = {"role", "too_junior", "too_senior", "tech", "sector"}

# Similarité entre deux offres à partir de laquelle elles sont considérées « du même genre ».
# Mesurée sur les embeddings nomic : médiane entre deux offres ≈ 0,72, 90e centile ≈ 0,80.
SIMILAR_FROM, SIMILAR_FULL = 0.78, 0.95


@dataclass(frozen=True)
class Feedback:
    job_id: int
    rating: int
    reasons: list[str]
    comment: str | None
    title: str
    company: str | None
    updated_at: datetime

    @property
    def liked(self) -> bool:
        return self.rating > 0

    @property
    def reason_labels(self) -> list[str]:
        return [REASONS.get(r, r) for r in self.reasons]


@dataclass
class FeedbackSignals:
    """Avis figés au lancement d'une recherche."""

    hidden_job_ids: list[int] = field(default_factory=list)
    liked_job_ids: list[int] = field(default_factory=list)
    disliked_job_ids: list[int] = field(default_factory=list)
    digest: str = ""

    @property
    def count(self) -> int:
        return len(self.hidden_job_ids) + len(self.liked_job_ids)


# --------------------------------------------------------------------------- #
# Lecture / écriture
# --------------------------------------------------------------------------- #


def set_feedback(
    user_id: int,
    job_id: int,
    rating: int,
    reasons: list[str] | None = None,
    comment: str | None = None,
    search_id: int | None = None,
) -> None:
    with session_scope() as session:
        fb = session.scalar(
            select(JobFeedback).where(JobFeedback.user_id == user_id, JobFeedback.job_id == job_id)
        )
        if fb is None:
            fb = JobFeedback(user_id=user_id, job_id=job_id)
            session.add(fb)
        fb.rating = LIKE if rating > 0 else DISLIKE
        fb.reasons = [r for r in (reasons or []) if r in REASONS] if fb.rating == DISLIKE else []
        fb.comment = (comment or "").strip()[:1000] or None
        fb.search_id = search_id or fb.search_id


def delete_feedback(user_id: int, job_id: int) -> None:
    with session_scope() as session:
        session.execute(
            delete(JobFeedback).where(JobFeedback.user_id == user_id, JobFeedback.job_id == job_id)
        )


def list_feedback(user_id: int) -> list[Feedback]:
    with session_scope() as session:
        rows = session.execute(
            select(JobFeedback, JobOffer.title, JobOffer.company)
            .join(JobOffer, JobOffer.id == JobFeedback.job_id)
            .where(JobFeedback.user_id == user_id)
            .order_by(JobFeedback.updated_at.desc())
        ).all()
        return [
            Feedback(
                job_id=fb.job_id,
                rating=fb.rating,
                reasons=list(fb.reasons or []),
                comment=fb.comment,
                title=title,
                company=company,
                updated_at=fb.updated_at,
            )
            for fb, title, company in rows
        ]


def feedback_map(user_id: int) -> dict[int, Feedback]:
    return {fb.job_id: fb for fb in list_feedback(user_id)}


def exclude_company(user_id: int, company: str) -> None:
    """Ajoute l'entreprise aux exclusions de la fiche de recherche."""
    company = company.strip()
    if not company:
        return
    with session_scope() as session:
        profile = session.get(UserProfile, user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id)
            session.add(profile)
        current = list(profile.exclude_companies or [])
        if company.lower() not in {c.lower() for c in current}:
            profile.exclude_companies = [*current, company]


# --------------------------------------------------------------------------- #
# Utilisation par les recherches
# --------------------------------------------------------------------------- #


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_digest(feedback: list[Feedback], max_items: int = 6, max_chars: int = 1200) -> str:
    """Résumé compact et déterministe des avis (aucun appel LLM), du plus récent au plus ancien."""
    if not feedback:
        return ""
    lines: list[str] = []
    disliked = [f for f in feedback if not f.liked and f.reasons != ["seen"]]
    liked = [f for f in feedback if f.liked]

    counts = Counter(r for f in disliked for r in f.reasons if r != "seen")
    if counts:
        lines.append(
            "Motifs de refus fréquents : "
            + ", ".join(f"{REASONS[r].lower()} ({n})" for r, n in counts.most_common(4))
        )
    for label, items in (("Offres appréciées", liked), ("Offres écartées", disliked)):
        if not items:
            continue
        lines.append(f"{label} :")
        for f in items[:max_items]:
            line = f"- {_short(f.title, 70)}" + (f" ({_short(f.company, 40)})" if f.company else "")
            if not f.liked and f.reasons:
                line += " — " + ", ".join(lab.lower() for lab in f.reason_labels)
            if f.comment:
                line += f" — « {_short(f.comment, 160)} »"
            lines.append(line)

    digest = ""
    for line in lines:  # coupe proprement à la limite de taille
        if len(digest) + len(line) + 1 > max_chars:
            break
        digest += line + "\n"
    return digest.strip()


def signals_from(feedback: list[Feedback], with_digest: bool = True) -> FeedbackSignals:
    return FeedbackSignals(
        hidden_job_ids=[f.job_id for f in feedback if not f.liked],
        liked_job_ids=[f.job_id for f in feedback if f.liked],
        disliked_job_ids=[
            f.job_id for f in feedback if not f.liked and (not f.reasons or set(f.reasons) & CONTENT_REASONS)
        ],
        digest=build_digest(feedback) if with_digest else "",
    )


def feedback_signals(user_id: int, with_digest: bool = True) -> FeedbackSignals:
    return signals_from(list_feedback(user_id), with_digest)


def closeness(similarity: float) -> float:
    """0 si deux offres sont peu semblables, 1 si quasi identiques (linéaire entre les seuils)."""
    return max(0.0, min(1.0, (similarity - SIMILAR_FROM) / (SIMILAR_FULL - SIMILAR_FROM)))


def feedback_adjustment(max_sim_liked: float | None, max_sim_disliked: float | None, points: float) -> float:
    """Points ajoutés au score d'une offre selon sa proximité avec les offres aimées / écartées."""
    bonus = closeness(max_sim_liked) if max_sim_liked is not None else 0.0
    malus = closeness(max_sim_disliked) if max_sim_disliked is not None else 0.0
    return round(points * (bonus - malus), 1)
