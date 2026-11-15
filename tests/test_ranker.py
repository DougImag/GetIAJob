from getiajob.agents.ranker import combine_scores, hard_filter_reason
from getiajob.config import Expectations, keyword_in
from getiajob.db import JobOffer


def make_job(title="Data Engineer", company="ACME", description="Python, Airflow, CDI"):
    return JobOffer(title=title, company=company, description=description)


def test_keyword_in_whole_word():
    assert keyword_in("java", "Java / Spring")
    assert not keyword_in("java", "JavaScript")
    assert keyword_in("C++", "Expert C++ requis")


def test_hard_filter_passes():
    assert hard_filter_reason(make_job(), Expectations()) is None


def test_hard_filter_excludes_keyword_and_company():
    exp = Expectations(exclude_keywords=["stage"], exclude_companies=["acme"])
    assert "entreprise exclue" in hard_filter_reason(make_job(), exp)
    exp = Expectations(exclude_keywords=["stage"])
    assert "stage" in hard_filter_reason(make_job(title="Stage Data Engineer"), exp)


def test_hard_filter_must_have():
    exp = Expectations(must_have_keywords=["python", "kubernetes"])
    assert "kubernetes" in hard_filter_reason(make_job(), exp)


def test_combine_scores():
    assert combine_scores(0.8, 90, 0.7) == 87.0
    assert combine_scores(0.8, None, 0.7) == 80.0
    assert combine_scores(1.4, 100, 0.5) == 100.0
