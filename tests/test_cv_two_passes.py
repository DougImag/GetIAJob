"""Découpage des sections et extraction des expériences en deux temps (petits modèles)."""

from types import SimpleNamespace

from langchain_core.runnables import RunnableLambda

from getiajob.agents import cv_analyzer
from getiajob.agents.cv_analyzer import _experience_chunks, _needs_two_passes, _run_task
from getiajob.cv_text import mark_headings, section_of, section_text
from getiajob.schemas import (
    CVProfile,
    Experience,
    ExperienceExtraction,
    ExperienceOutline,
    ExperienceOutlineList,
)
from getiajob.services import usage

CV = """PROFIL
Chef de projet data
EXPÉRIENCE
Chef de projet Data
09/2026
ACME Freelance
• Pilotage de la plateforme
Coach DevOps
11 ans
GROUPE BPCE
• Pipeline CI/CD
PROJETS
GetIAJob
FORMATION & CERTIFICATIONS
DUT Informatique"""


def test_compound_titles_and_employers():
    assert section_of("FORMATION & CERTIFICATIONS") == "education"
    assert section_of("LANGUES ET LOISIRS") == "languages"
    assert section_of("HTML/CSS") is None  # pas un titre : toutes les parties doivent être connues
    marked = mark_headings(CV)
    # Un employeur en majuscules, dans les expériences, n'est pas un titre de section
    assert "GROUPE BPCE" in marked.split("\n")
    assert "## GROUPE BPCE" not in marked
    assert "## FORMATION & CERTIFICATIONS" in marked


def test_section_text_stops_at_next_heading():
    section = section_text(mark_headings(CV), "experience")
    assert section.startswith("## EXPÉRIENCE")
    assert "GROUPE BPCE" in section and "GetIAJob" not in section
    assert section_text("## PROJETS\nx", "experience") == ""


def test_chunks_follow_titles():
    section = section_text(mark_headings(CV), "experience")
    first, second, missing = _experience_chunks(section, ["Chef de projet Data", "Coach DevOps", "Inconnu"])
    assert "Pilotage" in first and "Pipeline" not in first
    assert "GROUPE BPCE" in second and "Pilotage" not in second
    assert missing == section  # intitulé introuvable : toute la section


def test_needs_two_passes():
    text = mark_headings(CV)
    ok = ExperienceExtraction(experiences=[Experience(title="Coach DevOps")])
    stuffed = ExperienceExtraction(experiences=[Experience(title="Chef de projet, ACME, " + "x" * 200)])
    assert not _needs_two_passes(ok, text)
    assert _needs_two_passes(stuffed, text)
    assert _needs_two_passes(ValueError("tronqué"), text)
    assert _needs_two_passes(ExperienceExtraction(), text)
    assert not _needs_two_passes(ExperienceExtraction(), "## FORMATION\nMaster")  # pas d'expérience


def test_run_task_falls_back_to_two_passes(monkeypatch):
    def fake_structured(schema):
        def call(messages):
            if schema is ExperienceExtraction:
                raise ValueError("LengthFinishReasonError")  # réponse jamais terminée
            if schema is ExperienceOutlineList:
                return ExperienceOutlineList(
                    experiences=[
                        ExperienceOutline(title="Chef de projet Data", start="09/2026"),
                        ExperienceOutline(title="Coach DevOps", company="GROUPE BPCE", duration="11 ans"),
                    ]
                )
            extract = messages[1][1]
            if "Pipeline" in extract:
                raise ValueError("échec du détail")  # repli sur le repère de la 1re passe
            return Experience(title="Chef de projet Data", company="ACME", achievements=["Pilotage"])

        return RunnableLambda(call)

    monkeypatch.setattr(cv_analyzer, "structured", fake_structured)
    result = _run_task("experiences", mark_headings(CV))
    assert [e.title for e in result.experiences] == ["Chef de projet Data", "Coach DevOps"]
    first, second = result.experiences
    assert (first.company, first.start, first.achievements) == ("ACME", "09/2026", ["Pilotage"])
    assert (second.company, second.duration, second.achievements) == ("GROUPE BPCE", "11 ans", [])


def test_failed_sections_default_and_length_error_is_recorded(monkeypatch):
    assert CVProfile().failed_sections == []
    recorded = []
    monkeypatch.setattr(usage, "_record", lambda *args: recorded.append(args))
    counts = SimpleNamespace(
        prompt_tokens=5067,
        completion_tokens=6144,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=6144),
    )
    usage.record_failure(SimpleNamespace(completion=SimpleNamespace(usage=counts)), 16000)
    usage.record_failure(ValueError("sans usage"))
    assert recorded == [(5067, 6144, 6144, 16000)]


def test_repeated_experiences_and_misplaced_duration():
    from getiajob.agents.cv_analyzer import _dedupe_experiences, _fix_duration

    exps = [
        Experience(title="Responsable Digital Factory - Value Team", company="CITEO Freelance"),
        Experience(title="Coach DevOps", company="GROUPE BPCE"),
        Experience(title="Responsable Digital Factory", company="CITEO"),  # répétition du modèle
        Experience(title="Coach DevOps", company="SOCIÉTÉ GÉNÉRALE"),  # même poste, autre entreprise
    ]
    assert [(e.title, e.company) for e in _dedupe_experiences(exps)] == [
        (e.title, e.company) for e in (exps[0], exps[1], exps[3])
    ]
    fixed = _fix_duration(Experience(title="Coach", start="11 ans"))
    assert (fixed.start, fixed.duration) == (None, "11 ans")
    assert _fix_duration(Experience(title="Coach", start="2019")).start == "2019"
