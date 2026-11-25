"""Offres épinglées : une liste personnelle, envoyable par e-mail à l'utilisateur."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from getiajob.db import JobOffer, JobPin, session_scope
from getiajob.services.alerting import send_mail


@dataclass
class PinnedOffer:
    job_id: int
    title: str
    company: str | None
    location: str | None
    url: str


def pinned_ids(user_id: int) -> set[int]:
    with session_scope() as session:
        return set(session.scalars(select(JobPin.job_id).where(JobPin.user_id == user_id)))


def set_pin(user_id: int, job_id: int, pinned: bool) -> None:
    with session_scope() as session:
        if pinned:
            session.execute(insert(JobPin).values(user_id=user_id, job_id=job_id).on_conflict_do_nothing())
        else:
            session.execute(delete(JobPin).where(JobPin.user_id == user_id, JobPin.job_id == job_id))


def list_pins(user_id: int) -> list[PinnedOffer]:
    """Offres épinglées, de la plus récemment épinglée à la plus ancienne."""
    with session_scope() as session:
        rows = session.execute(
            select(JobOffer)
            .join(JobPin, JobPin.job_id == JobOffer.id)
            .where(JobPin.user_id == user_id)
            .order_by(JobPin.created_at.desc(), JobPin.id.desc())
        ).scalars()
        return [PinnedOffer(j.id, j.title, j.company, j.location, j.url) for j in rows]


def compose_pins_mail(
    offers: list[PinnedOffer], scores: dict[int, float] | None = None, today: date | None = None
) -> tuple[str, str, str]:
    """(objet, texte brut, HTML) du récapitulatif des offres épinglées."""
    scores = scores or {}
    today = today or date.today()
    n = len(offers)
    subject = f"GetIAJob — {n} offre{'s' if n > 1 else ''} épinglée{'s' if n > 1 else ''} ({today:%d/%m/%Y})"

    def details(o: PinnedOffer) -> list[str]:
        parts = [x for x in (o.company, o.location) if x]
        if o.job_id in scores:
            parts.append(f"score {scores[o.job_id]:.0f}/100")
        return parts

    text = [f"Tes offres épinglées sur GetIAJob ({n}) :", ""]
    for i, o in enumerate(offers, 1):
        text.append(f"{i}. {o.title}")
        if d := details(o):
            text.append(f"   {' · '.join(d)}")
        text.append(f"   {o.url}")
        text.append("")

    items = "".join(
        f'<li style="margin-bottom:12px"><a href="{escape(o.url, quote=True)}" '
        f'style="font-weight:600;color:#4f46e5">{escape(o.title)}</a>'
        + (f'<br><span style="color:#6b7280">{escape(" · ".join(d))}</span>' if (d := details(o)) else "")
        + "</li>"
        for o in offers
    )
    html = (
        '<div style="font-family:Arial,sans-serif;font-size:14px;color:#111827">'
        f"<p>Tes offres épinglées sur GetIAJob ({n}) :</p><ol>{items}</ol></div>"
    )
    return subject, "\n".join(text).rstrip() + "\n", html


def send_pins(user_id: int, email: str, scores: dict[int, float] | None = None) -> int:
    """Envoie les offres épinglées à l'adresse de l'utilisateur ; renvoie le nombre d'offres."""
    offers = list_pins(user_id)
    if not offers:
        return 0
    send_mail([email], *compose_pins_mail(offers, scores))
    return len(offers)
