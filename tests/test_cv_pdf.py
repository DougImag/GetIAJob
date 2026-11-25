from getiajob.cv_pdf import clean, emphasize, file_name, period, render_cv_pdf
from getiajob.schemas import CVProfile, Experience, SkillGroup


def test_render_cv_pdf():
    p = CVProfile(
        full_name="Élodie Martin",
        headline="Ingénieure IA → LLM & agents",
        summary="Sept ans d'expérience ≈ 95% de réussite.",
        experiences=[
            Experience(
                title="AI Engineer", company="Acme", start="2024", end="présent", achievements=["Gain de 30%"]
            )
        ],
        skills=[
            SkillGroup(category="IA", items=["LangGraph", "RAG"]),
            SkillGroup(category="ia", items=["rag"]),
        ],
    )
    pdf = render_cv_pdf(p)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000
    assert file_name(p) == "CV_Elodie_Martin.pdf"
    assert p.merged_skills() == [("IA", ["LangGraph", "RAG"])]


def test_text_helpers():
    assert clean("A → B ≈ C D") == "A -> B ~ C D"
    assert period("2024", "présent") == "2024 – Aujourd'hui"
    assert period("2021", "2021") == "2021"
    assert emphasize("Gain de 30% & ~100 URLs, >80%, en 2023") == (
        "Gain de <b>30%</b> &amp; <b>~100</b> URLs, <b>&gt;80%</b>, en 2023"
    )
    assert file_name(CVProfile()) == "CV_GetIAJob.pdf"
