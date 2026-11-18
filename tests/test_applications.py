"""Suivi des candidatures : prochaines étapes, compteurs (sans base)."""

from __future__ import annotations

from datetime import date, timedelta

from getiajob.services.applications import Application, Note, upcoming

TODAY = date(2026, 11, 15)


def _app(id: int, status: str = "applied", next_on: date | None = None, notes=()) -> Application:
    return Application(
        id=id,
        job_id=None,
        title=f"Poste {id}",
        company="ACME",
        location=None,
        url=None,
        source=None,
        status=status,
        applied_on=TODAY - timedelta(days=10),
        next_step="Entretien" if next_on else None,
        next_step_on=next_on,
        notes=list(notes),
    )


def test_upcoming_keeps_active_future_steps_sorted():
    apps = [
        _app(1, next_on=TODAY + timedelta(days=5)),
        _app(2, "interview", next_on=TODAY),
        _app(3, next_on=TODAY - timedelta(days=1)),  # passée
        _app(4, "rejected", next_on=TODAY + timedelta(days=1)),  # candidature close
        _app(5),
    ]
    assert [a.id for a in upcoming(apps, TODAY)] == [2, 1]


def test_interviews_count_only_interview_steps():
    notes = [
        Note(1, "interview_hr", TODAY, None),
        Note(2, "test", TODAY, None),
        Note(3, "note", TODAY, "x"),
        Note(4, "status", TODAY, "Postulé → Entretiens en cours"),
        Note(5, "followup", TODAY, None),
    ]
    a = _app(1, "interview", notes=notes)
    assert a.interviews == 2 and a.active and a.status_label == "Entretiens en cours"
    assert not _app(2, "withdrawn").active


def test_follow_up_suggested_after_silence():
    old = _app(1)  # postulé il y a 10 j, aucune nouvelle
    assert old.needs_follow_up(TODAY)
    recent = _app(2, notes=[Note(1, "followup", TODAY - timedelta(days=3), None)])
    assert not recent.needs_follow_up(TODAY)
    planned = _app(3, next_on=TODAY + timedelta(days=2))
    assert not planned.needs_follow_up(TODAY)
    assert not _app(4, "interview").needs_follow_up(TODAY)


def test_stage_follows_status():
    assert [_app(i, s).stage for i, s in enumerate(["applied", "interview", "offer", "rejected"])] == [
        0,
        1,
        2,
        3,
    ]


def test_result_card_shows_application_status():
    from datetime import date as _date

    from getiajob.services.applications import AppliedMark
    from getiajob.web.views.results import applied_label

    assert applied_label(AppliedMark(1, _date(2026, 11, 18), "applied")) == "Postulé le 18/11/2026"
    assert applied_label(AppliedMark(1, _date(2026, 11, 18), "withdrawn")) == "Abandonnée"
    assert applied_label(AppliedMark(1, _date(2026, 11, 18), "interview")) == "Entretiens en cours"
