"""Suivi des candidatures : « J'ai postulé », statut, prochaine étape et journal des entretiens."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from getiajob.db import ApplicationNote, CVRecord, JobApplication, JobMatch, JobOffer, session_scope

STATUSES = {
    "applied": "Postulé",
    "interview": "Entretiens en cours",
    "offer": "Offre reçue",
    "accepted": "Acceptée",
    "rejected": "Refusée",
    "withdrawn": "Abandonnée",
}
ACTIVE_STATUSES = ("applied", "interview", "offer")
STATUS_COLORS = {
    "applied": "blue",
    "interview": "violet",
    "offer": "orange",
    "accepted": "green",
    "rejected": "red",
    "withdrawn": "gray",
}

NOTE_KINDS = {
    "note": "Note",
    "call": "Échange téléphonique",
    "interview_hr": "Entretien RH",
    "interview_manager": "Entretien manager",
    "interview_tech": "Entretien technique",
    "test": "Test / cas pratique",
    "followup": "Relance",
    "feedback": "Retour reçu",
    "status": "Changement de statut",
}
NOTE_ICONS = {
    "note": ":material/edit_note:",
    "call": ":material/call:",
    "interview_hr": ":material/groups:",
    "interview_manager": ":material/supervisor_account:",
    "interview_tech": ":material/code:",
    "test": ":material/assignment:",
    "followup": ":material/forward_to_inbox:",
    "feedback": ":material/mark_email_read:",
    "status": ":material/flag:",
}
INTERVIEW_KINDS = {"call", "interview_hr", "interview_manager", "interview_tech", "test"}
# Étapes du parcours affichées en frise ; l'issue (acceptée, refusée, abandonnée) occupe la dernière.
STAGES = ["Candidature", "Entretiens", "Offre", "Décision"]
STAGE_INDEX = {"applied": 0, "interview": 1, "offer": 2, "accepted": 3, "rejected": 3, "withdrawn": 3}
FOLLOW_UP_DAYS = 10  # sans nouvelles depuis ce délai : suggérer une relance


class ApplicationError(ValueError):
    pass


@dataclass(frozen=True)
class Note:
    id: int
    kind: str
    happened_on: date
    comment: str | None

    @property
    def label(self) -> str:
        return NOTE_KINDS.get(self.kind, self.kind)


@dataclass(frozen=True)
class Application:
    id: int
    job_id: int | None
    title: str
    company: str | None
    location: str | None
    url: str | None
    source: str | None
    status: str
    applied_on: date
    next_step: str | None
    next_step_on: date | None
    notes: list[Note] = field(default_factory=list)
    salary: str | None = None
    score: float | None = None
    summary: str | None = None

    @property
    def status_label(self) -> str:
        return STATUSES.get(self.status, self.status)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def interviews(self) -> int:
        return sum(1 for n in self.notes if n.kind in INTERVIEW_KINDS)

    @property
    def stage(self) -> int:
        return STAGE_INDEX.get(self.status, 0)

    @property
    def last_activity(self) -> date:
        """Date du dernier événement connu (candidature ou étape du suivi)."""
        return max([self.applied_on, *(n.happened_on for n in self.notes)])

    def days_since(self, today: date | None = None) -> int:
        return ((today or date.today()) - self.applied_on).days

    def needs_follow_up(self, today: date | None = None) -> bool:
        """Candidature sans réponse ni prochaine étape depuis FOLLOW_UP_DAYS jours."""
        today = today or date.today()
        return (
            self.status == "applied"
            and not (self.next_step_on and self.next_step_on >= today)
            and (today - self.last_activity).days >= FOLLOW_UP_DAYS
        )


@dataclass(frozen=True)
class AppliedMark:
    id: int
    applied_on: date
    status: str


def _to_dc(a: JobApplication) -> Application:
    return Application(
        id=a.id,
        job_id=a.job_id,
        title=a.title,
        company=a.company,
        location=a.location,
        url=a.url,
        source=a.source,
        status=a.status,
        applied_on=a.applied_on,
        next_step=a.next_step,
        next_step_on=a.next_step_on,
        notes=[Note(n.id, n.kind, n.happened_on, n.comment) for n in a.notes],
        salary=a.salary,
        score=a.score,
        summary=a.summary,
    )


def best_match(session, user_id: int, job_id: int) -> tuple[float | None, str | None]:
    """Meilleur score de l'offre parmi les CV de l'utilisateur, avec la synthèse de l'IA."""
    row = session.execute(
        select(JobMatch.final_score, JobMatch.assessment)
        .join(CVRecord, CVRecord.id == JobMatch.cv_id)
        .where(CVRecord.user_id == user_id, JobMatch.job_id == job_id)
        .order_by(JobMatch.final_score.desc())
        .limit(1)
    ).first()
    if row is None:
        return None, None
    score, assessment = row
    return float(score), (assessment or {}).get("summary")


def _owned(session, user_id: int, application_id: int) -> JobApplication:
    app = session.scalar(
        select(JobApplication).where(JobApplication.id == application_id, JobApplication.user_id == user_id)
    )
    if app is None:
        raise ApplicationError("Candidature introuvable.")
    return app


# --------------------------------------------------------------------------- #
# Création
# --------------------------------------------------------------------------- #


def mark_applied(
    user_id: int,
    job_id: int,
    *,
    applied_on: date | None = None,
    comment: str | None = None,
    search_id: int | None = None,
    score: float | None = None,
    summary: str | None = None,
) -> int:
    """« J'ai postulé » sur une offre en base. Idempotent : renvoie la candidature existante.

    `score` / `summary` : ceux affichés dans la recherche d'où l'on postule ; à défaut, le meilleur
    score de l'offre parmi les CV de l'utilisateur.
    """
    with session_scope() as session:
        existing = session.scalar(
            select(JobApplication).where(JobApplication.user_id == user_id, JobApplication.job_id == job_id)
        )
        if existing is not None:
            return existing.id
        job = session.get(JobOffer, job_id)
        # Offre privée d'un autre utilisateur : invisible, donc inutilisable
        if job is None or (job.owner_user_id is not None and job.owner_user_id != user_id):
            raise ApplicationError("Offre introuvable.")
        if score is None:
            score, summary = best_match(session, user_id, job.id)
        app = JobApplication(
            user_id=user_id,
            job_id=job.id,
            search_id=search_id,
            title=job.title,
            company=job.company,
            location=job.location,
            url=job.url,
            source=job.source,
            salary=job.salary,
            score=score,
            summary=summary,
            status="applied",
            applied_on=applied_on or date.today(),
        )
        session.add(app)
        session.flush()
        if comment and comment.strip():
            session.add(
                ApplicationNote(
                    application_id=app.id, kind="note", happened_on=app.applied_on, comment=comment.strip()
                )
            )
        return app.id


def add_manual(
    user_id: int,
    title: str,
    *,
    company: str | None = None,
    location: str | None = None,
    url: str | None = None,
    applied_on: date | None = None,
    comment: str | None = None,
) -> int:
    """Candidature à une offre trouvée hors de GetIAJob."""
    if not title.strip():
        raise ApplicationError("L'intitulé du poste est obligatoire.")
    with session_scope() as session:
        app = JobApplication(
            user_id=user_id,
            title=title.strip()[:500],
            company=(company or "").strip()[:255] or None,
            location=(location or "").strip()[:255] or None,
            url=(url or "").strip()[:1000] or None,
            status="applied",
            applied_on=applied_on or date.today(),
        )
        session.add(app)
        session.flush()
        if comment and comment.strip():
            session.add(
                ApplicationNote(
                    application_id=app.id, kind="note", happened_on=app.applied_on, comment=comment.strip()
                )
            )
        return app.id


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #


def list_applications(user_id: int) -> list[Application]:
    with session_scope() as session:
        rows = session.scalars(
            select(JobApplication)
            .where(JobApplication.user_id == user_id)
            .options(selectinload(JobApplication.notes))
            .order_by(JobApplication.applied_on.desc(), JobApplication.id.desc())
        ).all()
        # Score manquant (candidature antérieure, offre notée depuis) : complété à l'affichage,
        # sans écriture en base (la page reste consultable en lecture seule).
        result = []
        for a in rows:
            item = _to_dc(a)
            if item.score is None and item.job_id is not None:
                score, summary = best_match(session, user_id, item.job_id)
                item = replace(item, score=score, summary=item.summary or summary)
            result.append(item)
        return result


def applied_map(user_id: int) -> dict[int, AppliedMark]:
    """job_id → candidature, pour marquer les offres déjà postulées dans les résultats."""
    with session_scope() as session:
        rows = session.execute(
            select(
                JobApplication.job_id, JobApplication.id, JobApplication.applied_on, JobApplication.status
            ).where(JobApplication.user_id == user_id, JobApplication.job_id.is_not(None))
        ).all()
        return {job_id: AppliedMark(app_id, on, status) for job_id, app_id, on, status in rows}


def upcoming(apps: list[Application], today: date | None = None) -> list[Application]:
    """Candidatures actives dont la prochaine étape est datée d'aujourd'hui ou plus tard (la plus proche
    d'abord)."""
    today = today or date.today()
    soon = [a for a in apps if a.active and a.next_step_on and a.next_step_on >= today]
    return sorted(soon, key=lambda a: a.next_step_on)


# --------------------------------------------------------------------------- #
# Modification
# --------------------------------------------------------------------------- #


def set_status(user_id: int, application_id: int, status: str, comment: str | None = None) -> None:
    """Change le statut ; le changement est consigné dans le journal de la candidature."""
    if status not in STATUSES:
        raise ApplicationError("Statut inconnu.")
    with session_scope() as session:
        app = _owned(session, user_id, application_id)
        if app.status == status:
            return
        previous = STATUSES.get(app.status, app.status)
        app.status = status
        text = f"{previous} → {STATUSES[status]}"
        if comment and comment.strip():
            text += f"\n{comment.strip()}"
        session.add(
            ApplicationNote(application_id=app.id, kind="status", happened_on=date.today(), comment=text)
        )
        if status not in ACTIVE_STATUSES:
            app.next_step, app.next_step_on = None, None


def set_next_step(user_id: int, application_id: int, label: str | None, on: date | None) -> None:
    with session_scope() as session:
        app = _owned(session, user_id, application_id)
        app.next_step = (label or "").strip()[:255] or None
        app.next_step_on = on if app.next_step or on else None


def update_details(
    user_id: int,
    application_id: int,
    *,
    title: str,
    company: str | None,
    location: str | None,
    url: str | None,
    applied_on: date,
) -> None:
    if not title.strip():
        raise ApplicationError("L'intitulé du poste est obligatoire.")
    with session_scope() as session:
        app = _owned(session, user_id, application_id)
        app.title = title.strip()[:500]
        app.company = (company or "").strip()[:255] or None
        app.location = (location or "").strip()[:255] or None
        app.url = (url or "").strip()[:1000] or None
        app.applied_on = applied_on


def add_note(user_id: int, application_id: int, kind: str, happened_on: date, comment: str | None) -> None:
    """Ajoute une étape au suivi. Un entretien fait passer une candidature « Postulé » en « Entretiens »."""
    if kind not in NOTE_KINDS or kind == "status":
        raise ApplicationError("Type d'étape inconnu.")
    if kind == "note" and not (comment or "").strip():
        raise ApplicationError("La note est vide.")
    with session_scope() as session:
        app = _owned(session, user_id, application_id)
        session.add(
            ApplicationNote(
                application_id=app.id,
                kind=kind,
                happened_on=happened_on,
                comment=(comment or "").strip() or None,
            )
        )
        if kind in INTERVIEW_KINDS and app.status == "applied":
            app.status = "interview"
            session.add(
                ApplicationNote(
                    application_id=app.id,
                    kind="status",
                    happened_on=happened_on,
                    comment=f"{STATUSES['applied']} → {STATUSES['interview']}",
                )
            )


def update_note(user_id: int, note_id: int, kind: str, happened_on: date, comment: str | None) -> None:
    with session_scope() as session:
        note = session.scalar(
            select(ApplicationNote)
            .join(JobApplication)
            .where(ApplicationNote.id == note_id, JobApplication.user_id == user_id)
        )
        if note is None:
            raise ApplicationError("Étape introuvable.")
        if note.kind != "status":
            if kind not in NOTE_KINDS or kind == "status":
                raise ApplicationError("Type d'étape inconnu.")
            note.kind = kind
        note.happened_on = happened_on
        note.comment = (comment or "").strip() or None


def delete_note(user_id: int, note_id: int) -> None:
    with session_scope() as session:
        note = session.scalar(
            select(ApplicationNote)
            .join(JobApplication)
            .where(ApplicationNote.id == note_id, JobApplication.user_id == user_id)
        )
        if note is not None:
            session.delete(note)


def delete_application(user_id: int, application_id: int) -> None:
    with session_scope() as session:
        session.delete(_owned(session, user_id, application_id))
