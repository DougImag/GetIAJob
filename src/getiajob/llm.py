"""Accès aux modèles open source servis par Ollama, via LangChain."""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any, TypeVar

from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_ollama import ChatOllama, OllamaEmbeddings
from pydantic import BaseModel

from getiajob.config import get_settings

M = TypeVar("M", bound=BaseModel)

# Nombre maximal d'éléments par liste dans une réponse structurée. Les petits modèles partent parfois
# en boucle dans une liste (« Oracle, JAVA, Oracle, JAVA… », « MSSQL CU179, CU180… ») jusqu'à remplir
# tout le contexte : 40 minutes sans GPU. La grammaire d'Ollama respecte « maxItems » et ferme la liste.
MAX_LIST_ITEMS = 30


@lru_cache
def get_chat_model() -> ChatOllama:
    s = get_settings()
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
    """Schéma JSON envoyé à Ollama : listes bornées, clés de premier niveau obligatoires."""

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


def structured(schema: type[M]) -> Runnable[Any, M]:
    """Équivalent de `with_structured_output(schema)`, avec les listes bornées (cf. MAX_LIST_ITEMS)."""

    def parse(message: BaseMessage) -> M:
        return schema.model_validate_json(message.content)

    return get_chat_model().bind(format=bounded_schema(schema)) | RunnableLambda(parse)


@lru_cache
def get_embeddings() -> OllamaEmbeddings:
    s = get_settings()
    return OllamaEmbeddings(model=s.embedding_model, base_url=s.ollama_base_url)
