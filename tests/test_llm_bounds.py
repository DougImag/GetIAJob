from pydantic import BaseModel

from getiajob.llm import MAX_LIST_ITEMS, bounded_schema
from getiajob.schemas import ExperienceExtraction, SkillsExtraction


def _arrays(node):
    if isinstance(node, dict):
        if node.get("type") == "array":
            yield node
        for value in node.values():
            yield from _arrays(value)
    elif isinstance(node, list):
        for value in node:
            yield from _arrays(value)


def test_every_list_is_bounded():
    for schema in (SkillsExtraction, ExperienceExtraction):
        arrays = list(_arrays(bounded_schema(schema)))
        assert arrays, schema
        assert all(a["maxItems"] == MAX_LIST_ITEMS for a in arrays)


def test_pydantic_schema_left_untouched():
    bounded_schema(SkillsExtraction)
    assert "maxItems" not in str(SkillsExtraction.model_json_schema())


def test_parse_with_structured_runnable(monkeypatch):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    import getiajob.llm as llm

    class Out(BaseModel):
        items: list[str]

    fake = RunnableLambda(lambda _: AIMessage(content='{"items": ["a", "b"]}'))
    fake.bind = lambda **kw: fake  # type: ignore[method-assign]
    monkeypatch.setattr(llm, "get_chat_model", lambda: fake)
    assert llm.structured(Out).invoke("x") == Out(items=["a", "b"])


def test_top_level_keys_required():
    js = bounded_schema(SkillsExtraction)
    assert set(js["required"]) == {"skills", "soft_skills", "languages", "interests"}
    # Le modèle pydantic, lui, garde ses valeurs par défaut
    assert SkillsExtraction.model_validate({}).skills == []
