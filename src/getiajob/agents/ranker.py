"""Agent 3 — Tri : filtres durs, pré-sélection sémantique (pgvector), puis évaluation LLM."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from getiajob.config import (
    Expectations,
    Preferences,
    get_settings,
    keyword_in,
    preferences_from_state,
)
from getiajob.db import CVRecord, JobMatch, JobOffer, session_scope
from getiajob.llm import get_embeddings, structured
from getiajob.schemas import CVProfile, JobAssessment
from getiajob.seniority import level_check, level_prompt, max_years
from getiajob.services.feedback import feedback_adjustment
from getiajob.services.monitoring import record, timed

log = logging.getLogger(__name__)

ASSESS_PROMPT = """Tu es un conseiller en recrutement exigeant et honnête. Évalue l'adéquation
entre un candidat et une offre d'emploi, en tenant compte de ses compétences, de sa séniorité,
de la localisation et de ses attentes. Barème du score :
- 85-100 : correspondance excellente, candidature prioritaire
- 70-84 : bonne correspondance, quelques écarts mineurs
- 50-69 : correspondance partielle
- < 50 : peu pertinent
Ne valorise pas une offre sur des critères absents de la description.
Respecte le niveau de poste visé par le candidat : une offre qui exige nettement plus d'expérience
que ce niveau (ex. « 5 ans d'expérience », poste senior / lead / expert pour un débutant) ne dépasse
pas 45, même si les compétences correspondent ; une offre ouverte aux débutants est un atout.
Si le candidat a donné des retours sur des offres déjà proposées, applique-les : pénalise une offre
qui présente les défauts qu'il a signalés, valorise celle qui ressemble à ce qu'il a apprécié."""


def job_text(job: JobOffer, max_chars: int = 4000) -> str:
    return "\n".join(
        filter(None, [job.title, job.company, job.location, (job.description or "")[:max_chars]])
    )


def hard_filter_reason(job: JobOffer, exp: Expectations, levels: list[str] | None = None) -> str | None:
    """Renvoie la raison d'exclusion de l'offre, ou None si elle passe les filtres.

    `levels` : niveaux d'expérience visés ; les offres nettement trop senior sont écartées.
    """
    company = (job.company or "").lower()
    if any(c.lower() in company for c in exp.exclude_companies if c):
        return f"entreprise exclue ({job.company})"
    haystack = f"{job.title}\n{job.description or ''}"
    for kw in exp.exclude_keywords:
        if keyword_in(kw, haystack):
            return f"mot-clé exclu : {kw}"
    missing = [kw for kw in exp.must_have_keywords if not keyword_in(kw, haystack)]
    if missing:
        return f"mots-clés manquants : {', '.join(missing)}"
    if levels:
        reason, _ = level_check(job.title, job.description, job.criteria, levels)
        return reason
    return None


def combine_scores(
    similarity: float, llm_score: float | None, llm_weight: float, adjustment: float = 0.0
) -> float:
    sim_pct = max(0.0, min(1.0, similarity)) * 100
    score = sim_pct if llm_score is None else llm_weight * llm_score + (1 - llm_weight) * sim_pct
    return round(max(0.0, min(100.0, score + adjustment)), 1)


def _unit(vectors) -> np.ndarray:
    m = np.asarray(vectors, dtype=np.float32)
    return m / np.clip(np.linalg.norm(m, axis=1, keepdims=True), 1e-9, None)


def feedback_adjustments(session: Session, jobs: list[JobOffer], ranking) -> dict[int, float]:
    """Bonus/malus (points) de chaque offre selon sa proximité avec les offres aimées / écartées.

    Calculé avec les embeddings déjà en base : aucun appel au LLM.
    """
    if not (ranking.liked_job_ids or ranking.disliked_job_ids) or not ranking.feedback_points:
        return {}
    refs = dict(
        session.execute(
            select(JobOffer.id, JobOffer.embedding).where(
                JobOffer.id.in_(ranking.liked_job_ids + ranking.disliked_job_ids),
                JobOffer.embedding.is_not(None),
            )
        ).all()
    )
    liked = [refs[i] for i in ranking.liked_job_ids if i in refs]
    disliked = [refs[i] for i in ranking.disliked_job_ids if i in refs]
    if not (liked or disliked):
        return {}
    candidates = _unit([j.embedding for j in jobs])
    max_liked = (candidates @ _unit(liked).T).max(axis=1) if liked else [None] * len(jobs)
    max_disliked = (candidates @ _unit(disliked).T).max(axis=1) if disliked else [None] * len(jobs)
    return {
        job.id: feedback_adjustment(
            None if ml is None else float(ml),
            None if md is None else float(md),
            ranking.feedback_points,
        )
        for job, ml, md in zip(jobs, max_liked, max_disliked, strict=True)
    }


def assessment_key(exp: Expectations, levels: list[str] | None = None) -> str:
    payload = json.dumps(
        {
            "exp": exp.model_dump(mode="json"),
            "levels": sorted(levels or []),
            "model": get_settings().llm_model,
            "prompt": hashlib.sha256(ASSESS_PROMPT.encode()).hexdigest()[:12],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def assess_job(
    profile: CVProfile, exp: Expectations, job: JobOffer, levels: list[str] | None = None
) -> JobAssessment:
    criteria = ", ".join(f"{k} : {v}" for k, v in (job.criteria or {}).items())
    offer = (
        f"Titre : {job.title}\nEntreprise : {job.company}\nLieu : {job.location}\n"
        f"Salaire : {job.salary or 'non précisé'}\nCritères : {criteria or 'n/a'}\n\n"
        f"Description :\n{(job.description or '')[:6000]}"
    )
    wanted = f"Niveau de poste visé : {level_prompt(levels)}\n" if levels else ""
    llm = structured(JobAssessment)
    with timed("llm", "evaluation"):
        return llm.invoke(
            [
                ("system", ASSESS_PROMPT),
                (
                    "human",
                    f"## Profil du candidat\n{profile.to_text()}\n\n"
                    f"## Attentes du candidat\n{wanted}{exp.to_prompt()}\n\n## Offre\n{offer}",
                ),
            ]
        )


def top_matches(
    session: Session,
    cv_id: int,
    limit: int,
    since: datetime | None = None,
    job_ids: list[int] | None = None,
    exclude_ids: list[int] | None = None,
    adjustments: dict[int, float] | None = None,
) -> list[dict]:
    adjustments = adjustments or {}
    query = (
        select(JobMatch, JobOffer)
        .join(JobOffer, JobMatch.job_id == JobOffer.id)
        .where(JobMatch.cv_id == cv_id)
        .order_by(JobMatch.final_score.desc())
        .limit(limit)
    )
    if since is not None:
        query = query.where(JobOffer.last_seen_at >= since)
    if job_ids is not None:
        query = query.where(JobOffer.id.in_(job_ids))
    if exclude_ids:
        query = query.where(JobOffer.id.not_in(exclude_ids))
    rows = session.execute(query).all()
    results = []
    for match, job in rows:
        a = match.assessment or {}
        results.append(
            {
                "job_id": job.id,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "url": job.url,
                "source": job.source,
                "posted_at": job.posted_at.date().isoformat() if job.posted_at else None,
                "salary": job.salary,
                "similarity": round(match.similarity, 3),
                "llm_score": match.llm_score,
                "final_score": match.final_score,
                "feedback_adjustment": adjustments.get(job.id, 0.0),
                "summary": a.get("summary"),
                "pros": a.get("pros", []),
                "cons": a.get("cons", []),
                "matching_skills": a.get("matching_skills", []),
                "missing_skills": a.get("missing_skills", []),
            }
        )
    return results


def rank_jobs(
    cv_id: int, prefs: Preferences, *, job_ids: list[int] | None = None, force: bool = False
) -> list[dict]:
    """Classe les offres pour un CV.

    `job_ids` restreint le tri aux offres collectées par la recherche en cours ; sinon toutes les
    offres vues sur la période `prefs.search.days` sont prises en compte.
    """
    since = datetime.now(UTC) - timedelta(days=prefs.search.days)
    exp, ranking = prefs.expectations, prefs.ranking
    levels = list(prefs.search.experience_levels)
    if max_years(levels) is None:
        levels = []  # niveau confirmé ou plus accepté : pas de contrainte de séniorité
    key = assessment_key(exp, levels)
    embeddings = get_embeddings()

    with session_scope() as session:
        cv = session.get(CVRecord, cv_id)
        if cv is None:
            raise ValueError(f"CV {cv_id} introuvable")
        if not cv.profile:
            raise ValueError(f"CV {cv_id} non analysé")
        profile = CVProfile.model_validate(cv.profile)
        if cv.embedding is None:
            with timed("embedding", "cv"):
                cv.embedding = embeddings.embed_query(profile.to_text())

        scope = JobOffer.id.in_(job_ids) if job_ids is not None else JobOffer.last_seen_at >= since
        # Offres privées (alertes e-mail) : seulement celles du propriétaire du CV.
        visible = or_(JobOffer.owner_user_id.is_(None), JobOffer.owner_user_id == cv.user_id)
        jobs = session.scalars(
            select(JobOffer).where(scope, visible, JobOffer.description.is_not(None))
        ).all()
        if not jobs:
            log.warning("Aucune offre récente en base. Lance d'abord la collecte.")
            return []

        to_embed = [j for j in jobs if j.embedding is None]
        if to_embed:
            log.info("Calcul des embeddings de %d offres…", len(to_embed))
            with timed("embedding", "offres", count=len(to_embed)):
                vectors = embeddings.embed_documents([job_text(j) for j in to_embed])
            for job, vector in zip(to_embed, vectors, strict=True):
                job.embedding = vector
            session.flush()

        hidden = set(ranking.hidden_job_ids)
        kept: list[JobOffer] = []
        reasons: list[str] = []
        for job in jobs:
            if job.id in hidden:
                log.debug("Exclue « %s » : écartée par l'utilisateur", job.title)
            elif reason := hard_filter_reason(job, exp, levels):
                log.debug("Exclue « %s » : %s", job.title, reason)
                reasons.append(reason)
            else:
                kept.append(job)
        log.info("%d/%d offres passent les filtres.", len(kept), len(jobs))
        if off_level := sum(1 for r in reasons if r.startswith(("niveau", "stage"))):
            log.info("Niveau visé (%s) : %d offre(s) écartée(s).", level_prompt(levels), off_level)
        if not kept:
            return []

        # Pré-sélection : similarité CV ↔ offre, ajustée par les avis (offres aimées / écartées)
        adjustments = feedback_adjustments(session, kept, ranking)
        if adjustments:
            moved = sum(1 for v in adjustments.values() if v)
            log.info("Avis de l'utilisateur : %d offre(s) ajustée(s).", moved)
        # … et par le niveau : bonus pour une offre ouverte aux débutants, malus si « confirmé ».
        level_adj = (
            {j.id: level_check(j.title, j.description, j.criteria, levels)[1] for j in kept} if levels else {}
        )
        total_adj = {
            i: adjustments.get(i, 0.0) + level_adj.get(i, 0.0) for i in adjustments.keys() | level_adj.keys()
        }
        similarity = (1 - JobOffer.embedding.cosine_distance(cv.embedding)).label("similarity")
        scored = session.execute(
            select(JobOffer, similarity).where(JobOffer.id.in_([j.id for j in kept]))
        ).all()
        candidates = sorted(
            scored, key=lambda row: row[1] + total_adj.get(row[0].id, 0.0) / 100, reverse=True
        )[: ranking.prefilter_top_k]

        cache_hits = 0
        for i, (job, sim) in enumerate(candidates, 1):
            match = session.scalar(select(JobMatch).where(JobMatch.cv_id == cv_id, JobMatch.job_id == job.id))
            if match is None:
                match = JobMatch(cv_id=cv_id, job_id=job.id)
                session.add(match)
            match.similarity = float(sim)

            if force or match.llm_score is None or match.assessment_key != key:
                log.info("[%d/%d] Évaluation : %s — %s", i, len(candidates), job.title, job.company)
                try:
                    assessment = assess_job(profile, exp, job, levels)
                    match.llm_score = assessment.score
                    match.assessment = assessment.model_dump()
                    match.assessment_key = key
                except Exception as exc:
                    log.warning("Évaluation impossible pour %s : %s", job.external_id, exc)
            else:
                cache_hits += 1

            match.final_score = combine_scores(
                match.similarity, match.llm_score, ranking.llm_weight, total_adj.get(job.id, 0.0)
            )
            session.commit()

        if cache_hits:
            record("cache", "evaluation", count=cache_hits)
        # Seules les offres notées ici : une offre sortie de la pré-sélection garderait sinon une
        # ancienne note, calculée avec d'autres critères.
        scored_ids = [job.id for job, _ in candidates]
        return top_matches(session, cv_id, ranking.top_n, job_ids=scored_ids, adjustments=adjustments)


def ranker_node(state: dict) -> dict:
    results = rank_jobs(
        state["cv_id"],
        preferences_from_state(state),
        job_ids=state.get("job_ids"),
        force=state.get("force_rank", False),
    )
    return {"results": results}
