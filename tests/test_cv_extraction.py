import pymupdf

from getiajob.agents.cv_analyzer import _clean, _contact_fallback, _ground_lists, _norm
from getiajob.cv_text import (
    collapse_spaced_letters,
    detected_sections,
    extract_cv_text,
    mark_headings,
    section_of,
)
from getiajob.schemas import CV_SCHEMA_VERSION, CVProfile, Experience, Language, SkillGroup


def _two_column_pdf() -> bytes:
    """CV synthétique : barre latérale à gauche, colonne principale à droite, lignes entrelacées."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    put = page.insert_text
    put((200, 40), "Jeanne DUPONT", fontsize=18)
    # Ordre d'écriture volontairement mélangé entre les colonnes
    put((200, 100), "E X P E R I E N C E S", fontsize=11)
    put((20, 100), "LANGUES", fontsize=11)
    put((200, 120), "2024 Data Engineer chez ACME", fontsize=9)
    put((20, 120), "Anglais : C1", fontsize=9)
    put((200, 135), "Pipelines Airflow et dbt", fontsize=9)
    put((20, 135), "Espagnol : B1", fontsize=9)
    put((200, 200), "FORMATION", fontsize=11)
    put((20, 200), "CENTRES", fontsize=11)
    put((20, 213), "D'INTÉRÊT", fontsize=11)
    put((200, 220), "2020-2023 Master Data Science", fontsize=9)
    put((20, 230), "Escalade", fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


def test_pdf_columns_are_read_one_after_the_other():
    text = extract_cv_text(_two_column_pdf(), "cv.pdf")
    lines = [line for line in text.split("\n") if line]

    # La colonne gauche est lue d'un bloc, puis la colonne droite : chaque titre suit son contenu
    assert lines.index("## LANGUES") < lines.index("Anglais : C1") < lines.index("Espagnol : B1")
    assert lines.index("## EXPERIENCES") < lines.index("2024 Data Engineer chez ACME")
    assert lines.index("2024 Data Engineer chez ACME") < lines.index("Pipelines Airflow et dbt")
    assert lines.index("Pipelines Airflow et dbt") < lines.index("## FORMATION")
    # Aucune ligne de la barre latérale n'est intercalée dans une expérience
    exp = lines.index("2024 Data Engineer chez ACME")
    assert lines[exp + 1] == "Pipelines Airflow et dbt"
    # Titre sur deux lignes fusionné
    assert "## CENTRES D'INTÉRÊT" in lines
    assert set(detected_sections(text)) >= {"LANGUES", "EXPERIENCES", "FORMATION", "CENTRES D'INTÉRÊT"}


def test_heading_helpers():
    assert collapse_spaced_letters("E X P E R I E N C E S") == "EXPERIENCES"
    assert collapse_spaced_letters("Python, C, R") == "Python, C, R"
    assert section_of("Expériences professionnelles :") == "experience"
    assert section_of("CENTRES D’INTÉRÊT") == "interests"
    assert section_of("Développement d'un module") is None
    marked = mark_headings("P R O J E T S\nCompilateur Java\nsamuel@example.com")
    assert marked.split("\n") == ["## PROJETS", "Compilateur Java", "samuel@example.com"]


def test_legacy_profile_is_readable_and_outdated():
    legacy = {
        "full_name": None,
        "headline": "Ingénieur IA",
        "summary": "",
        "seniority": "junior",
        "hard_skills": ["Python", "SQL"],
        "soft_skills": [],
        "languages": ["Anglais B2"],
        "education": ["ENSIMAG"],
        "locations": ["Houilles"],
        "experiences": [{"title": "Stage", "company": "X", "period": "2025", "highlights": ["RAG"]}],
        "target_job_titles": [],
        "industries": [],
        "certifications": [],
    }
    profile = CVProfile.model_validate(legacy)
    assert profile.is_outdated
    assert profile.hard_skills == ["Python", "SQL"]
    assert profile.languages[0].language == "Anglais B2"
    assert profile.education[0].degree == "ENSIMAG"
    assert profile.experiences[0].achievements == ["RAG"]
    assert profile.contact.location == "Houilles"
    assert not CVProfile(headline="x").is_outdated
    assert CVProfile().schema_version == CV_SCHEMA_VERSION


def test_clean_drops_placeholders_and_invented_dates():
    text = "Stage Technip 2026 - 6 MOIS"
    data = {
        "experiences": [
            {
                "title": "Stage",
                "location": "Non spécifié",
                "start": "2026",
                "end": "2026-06",
                "duration": "6 mois",
            }
        ],
        "soft_skills": ["", "Curieux"],
    }
    cleaned = _clean(data, _norm(text))
    exp = cleaned["experiences"][0]
    assert exp["location"] is None
    assert exp["start"] == "2026"
    assert exp["end"] is None  # date « calculée » absente du CV
    assert exp["duration"] == "6 mois"
    assert cleaned["soft_skills"] == ["Curieux"]


def test_contact_fallback_uses_regex():
    text = "Samuel\n+33 7 82 71 01 30\nsam.test@example.com\nin/samuel-f-123\n78800 HOUILLES\n"
    profile = CVProfile()
    profile.contact.email = "inventé@exemple.fr"
    _contact_fallback(profile, text)
    assert profile.contact.email == "sam.test@example.com"
    assert profile.contact.phone == "+33 7 82 71 01 30"
    assert profile.contact.linkedin == "in/samuel-f-123"
    assert profile.contact.location == "78800 HOUILLES"


def test_ground_lists_removes_hallucinations():
    text = "COMPÉTENCES\n• Programmation Python\n• Pédagogie\nLANGUES\nFrançais\nLOISIRS\nSki, Manga"
    profile = CVProfile(
        skills=[
            SkillGroup(category="Langages", items=["Python", "Rust"]),
            SkillGroup(category="X", items=["Pédagogie"]),
        ],
        soft_skills=["Pédagogie", "Dynamique"],
        languages=[Language(language="Français"), Language(language="Allemand")],
        interests=["Ski", "Manga", "Tennis"],
    )
    _ground_lists(profile, _norm(text))
    assert [(g.category, g.items) for g in profile.skills] == [("Langages", ["Python"])]
    assert profile.soft_skills == ["Pédagogie"]
    assert [lang.language for lang in profile.languages] == ["Français"]
    assert profile.interests == ["Ski", "Manga"]


def test_contract_defaults_to_cdi_when_not_specified():
    assert Experience(title="Chef de projet IT", company="ACME").contract_type == "CDI"
    assert Experience(title="Data Engineer", contract_type="Freelance").contract_type == "Freelance"
    assert Experience(title="Stage Data Scientist").contract_type == "Stage"
    assert Experience(title="Développeur", company="Freelance").contract_type == "Freelance"
    assert Experience(title="Ingénieur en alternance").contract_type == "Alternance"
    # Profil déjà enregistré sans contrat : complété à la lecture
    profile = CVProfile.model_validate({"schema_version": 2, "experiences": [{"title": "PMO"}]})
    assert profile.experiences[0].contract_type == "CDI"
