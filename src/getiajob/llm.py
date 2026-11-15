"""Accès aux modèles open source servis par Ollama, via LangChain."""

from __future__ import annotations

from functools import lru_cache

from langchain_ollama import ChatOllama, OllamaEmbeddings

from getiajob.config import get_settings


@lru_cache
def get_chat_model() -> ChatOllama:
    s = get_settings()
    return ChatOllama(
        model=s.llm_model,
        base_url=s.ollama_base_url,
        temperature=s.llm_temperature,
        # Le contexte par défaut d'Ollama est trop court pour un CV + une offre complète.
        num_ctx=s.llm_num_ctx,
    )


@lru_cache
def get_embeddings() -> OllamaEmbeddings:
    s = get_settings()
    return OllamaEmbeddings(model=s.embedding_model, base_url=s.ollama_base_url)
