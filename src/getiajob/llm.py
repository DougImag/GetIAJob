"""Accès aux modèles via LangChain : LLM sur Ollama ou sur une API compatible OpenAI (embeddings : Ollama)."""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from typing import Any, TypeVar

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_ollama import ChatOllama, OllamaEmbeddings
from pydantic import BaseModel

from getiajob.config import get_settings

M = TypeVar("M", bound=BaseModel)

# Nombre maximal d'éléments par liste dans une réponse structurée. Les petits modèles partent parfois
# en boucle dans une liste (« Oracle, JAVA, Oracle, JAVA… », « MSSQL CU179, CU180… ») jusqu'à remplir
# tout le contexte : 40 minutes sans GPU. La grammaire d'Ollama respecte « maxItems » et ferme la liste.
MAX_LIST_ITEMS = 30

# Bloc de raisonnement que certains modèles (Olala, Qwen3…) laissent dans la réponse.
_THINK = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def uses_ollama_llm() -> bool:
    return get_settings().llm_provider == "ollama"


def llm_endpoint() -> str:
    """Adresse du LLM, pour l'affichage et les diagnostics."""
    s = get_settings()
    return s.ollama_base_url if uses_ollama_llm() else s.llm_base_url


def _drop_empty_auth(request: httpx.Request) -> None:
    # Le client OpenAI envoie toujours « Authorization: Bearer … » ; sans clé, on le retire pour
    # utiliser l'accès anonyme du fournisseur (OVH AI Endpoints) au lieu d'un refus 401.
    if request.headers.get("Authorization") == "Bearer anonymous":
        del request.headers["Authorization"]


async def _drop_empty_auth_async(request: httpx.Request) -> None:
    _drop_empty_auth(request)


@lru_cache
def get_chat_model() -> BaseChatModel:
    s = get_settings()
    if s.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        anonymous = not s.llm_api_key
        return ChatOpenAI(
            model=s.llm_model,
            base_url=s.llm_base_url,
            api_key=s.llm_api_key or "anonymous",
            temperature=s.llm_temperature,
            max_tokens=s.llm_max_tokens,
            timeout=s.llm_timeout,
            max_retries=s.llm_max_retries,
            http_client=httpx.Client(event_hooks={"request": [_drop_empty_auth]} if anonymous else {}),
            http_async_client=httpx.AsyncClient(
                event_hooks={"request": [_drop_empty_auth_async]} if anonymous else {}
            ),
        )
    return ChatOllama(
        model=s.llm_model,
        base_url=s.ollama_base_url,
        temperature=s.llm_temperature,
        # Le contexte par défaut d'Ollama est trop court pour un CV + une offre complète.
        num_ctx=s.llm_num_ctx,
        # Plafond de la réponse : borne la durée d'un appel qui partirait en boucle.
        num_predict=s.llm_max_tokens,
        # Sans délai, un appel bloqué (mémoire saturée…) ferait attendre l'analyse indéfiniment.
        client_kwargs={"timeout": s.llm_timeout},
    )


def bounded_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Schéma JSON envoyé au LLM : listes bornées, clés de premier niveau obligatoires."""

    def cap(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "array":
                node.setdefault("maxItems", MAX_LIST_ITEMS)
            for value in node.values():
                cap(value)
        elif isinstance(node, list):
            for value in node:
                cap(value)

    js = copy.deepcopy(schema.model_json_schema())
    cap(js)
    # Clés de premier niveau obligatoires : sinon un petit modèle répond souvent « {} » (tout est facultatif)
    js["required"] = list(js.get("properties", {}))
    return js


def clean_json(content: Any) -> str:
    """Texte JSON d'une réponse : sans bloc de raisonnement ni balises Markdown ```json."""
    if isinstance(content, list):  # contenu en morceaux (certains fournisseurs)
        content = "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
    text = _FENCE.sub("", _THINK.sub("", content).strip()).strip()
    # Texte parasite autour de l'objet : on garde du premier « { » au dernier « } »
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def _with_schema_hint(js: dict[str, Any]) -> RunnableLambda:
    """Mode json_object : le schéma n'est pas imposé par l'API, on le donne au modèle dans le prompt."""
    hint = SystemMessage(
        "Réponds uniquement par un objet JSON valide, sans texte autour, conforme à ce schéma JSON :\n"
        + json.dumps(js, ensure_ascii=False)
    )

    def add(value: Any) -> list[Any]:
        messages = value.to_messages() if isinstance(value, PromptValue) else value
        if isinstance(messages, str):
            messages = [("human", messages)]
        return [*messages, hint]

    return RunnableLambda(add)


def structured(schema: type[M]) -> Runnable[Any, M]:
    """Équivalent de `with_structured_output(schema)`, avec les listes bornées (cf. MAX_LIST_ITEMS)."""

    def parse(message: BaseMessage) -> M:
        return schema.model_validate_json(clean_json(message.content))

    js = bounded_schema(schema)
    s = get_settings()
    if s.llm_provider == "ollama":
        return get_chat_model().bind(format=js) | RunnableLambda(parse)
    if s.llm_json_mode == "json_schema":
        response_format = {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": js}}
        return get_chat_model().bind(response_format=response_format) | RunnableLambda(parse)
    model = get_chat_model().bind(response_format={"type": "json_object"})
    return _with_schema_hint(js) | model | RunnableLambda(parse)


@lru_cache
def get_embeddings() -> OllamaEmbeddings:
    s = get_settings()
    return OllamaEmbeddings(model=s.embedding_model, base_url=s.ollama_base_url)
