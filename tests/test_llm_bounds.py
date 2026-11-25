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


def test_clean_json_strips_reasoning_and_fences():
    from getiajob.llm import clean_json

    raw = '<think>Le CV parle de Java…</think>\n```json\n{"items": ["a"]}\n```'
    assert clean_json(raw) == '{"items": ["a"]}'
    assert clean_json('Voici : {"a": 1} Bonne journée') == '{"a": 1}'
    assert clean_json([{"type": "text", "text": '{"a": 1}'}]) == '{"a": 1}'


def _openai_settings(monkeypatch, **extra):
    import getiajob.llm as llm
    from getiajob.config import Settings

    s = Settings(llm_provider="openai", llm_model="Olala-v1.0-mini-7A1B", **extra)
    monkeypatch.setattr(llm, "get_settings", lambda: s)
    return llm


def test_openai_provider_json_schema(monkeypatch):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    llm = _openai_settings(monkeypatch)
    seen = {}

    class Out(BaseModel):
        items: list[str]

    fake = RunnableLambda(lambda _: AIMessage(content='<think>…</think>{"items": ["a"]}'))

    def bind(**kw):
        seen.update(kw)
        return fake

    fake.bind = bind  # type: ignore[method-assign]
    monkeypatch.setattr(llm, "get_chat_model", lambda: fake)
    assert llm.structured(Out).invoke("x") == Out(items=["a"])
    fmt = seen["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["schema"]["properties"]["items"]["maxItems"] == MAX_LIST_ITEMS


def test_openai_provider_json_object_puts_schema_in_prompt(monkeypatch):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    llm = _openai_settings(monkeypatch, llm_json_mode="json_object")
    prompts = []

    class Out(BaseModel):
        items: list[str]

    def answer(messages):
        prompts.append(messages)
        return AIMessage(content='{"items": []}')

    fake = RunnableLambda(answer)
    fake.bind = lambda **kw: fake  # type: ignore[method-assign]
    monkeypatch.setattr(llm, "get_chat_model", lambda: fake)
    assert llm.structured(Out).invoke([("human", "CV")]) == Out(items=[])
    assert "maxItems" in prompts[0][-1].content


def test_openai_chat_model_without_key_is_anonymous(monkeypatch):
    import httpx

    llm = _openai_settings(monkeypatch)
    llm.get_chat_model.cache_clear()
    try:
        model = llm.get_chat_model()
        assert type(model).__name__ == "ChatOpenAI"
        request = httpx.Request("GET", "https://x/v1/models", headers={"Authorization": "Bearer anonymous"})
        llm._drop_empty_auth(request)
        assert "Authorization" not in request.headers
    finally:
        llm.get_chat_model.cache_clear()
