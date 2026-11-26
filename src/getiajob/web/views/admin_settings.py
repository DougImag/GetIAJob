"""Admin › Paramètres : LLM utilisé (parmi les configurations du .env) et limite de tokens."""

from __future__ import annotations

import httpx
import streamlit as st

from getiajob.config import LLMProfile, get_settings, llm_profiles
from getiajob.services import llm_settings
from getiajob.services.llm_settings import PERIODS, TokenLimit
from getiajob.services.supervision import Check, check_remote_llm
from getiajob.web.state import is_read_only, require_admin
from getiajob.web.views.admin_format import local, money_label, tokens_label

TEST_KEY = "settings_llm_test"

ENV_EXAMPLE = """# Une configuration par bloc LLM_PROFILES__<NOM>__<CHAMP>, puis scripts/restart.sh
# « DEFAULT » : utilisée tant qu'aucune autre n'est choisie (à défaut : les variables LLM_*), et
# réglages communs : adresse d'Ollama, contexte par défaut, embeddings (communs à toutes)
LLM_PROFILES__DEFAULT__LABEL=Qwen 2.5 7B (Ollama, local)
LLM_PROFILES__DEFAULT__PROVIDER=ollama
LLM_PROFILES__DEFAULT__MODEL=qwen2.5:7b
LLM_PROFILES__DEFAULT__BASE_URL=http://host.docker.internal:11434
LLM_PROFILES__DEFAULT__NUM_CTX=8192
LLM_PROFILES__DEFAULT__EMBEDDING_MODEL=nomic-embed-text
LLM_PROFILES__DEFAULT__EMBEDDING_DIM=768
LLM_PROFILES__DEFAULT__PRICE_INPUT=0
LLM_PROFILES__DEFAULT__PRICE_OUTPUT=0

# Autre modèle Ollama : BASE_URL et NUM_CTX facultatifs (ceux de DEFAULT)
LLM_PROFILES__GEMMA4__LABEL=Gemma 4 12B (Ollama, local)
LLM_PROFILES__GEMMA4__PROVIDER=ollama
LLM_PROFILES__GEMMA4__MODEL=gemma4:12b

LLM_PROFILES__OLALA__LABEL=Olala (OVHcloud, gratuit)
LLM_PROFILES__OLALA__PROVIDER=openai
LLM_PROFILES__OLALA__MODEL=Olala-v1.0-mini-7A1B
LLM_PROFILES__OLALA__BASE_URL=https://oai.endpoints.kepler.ai.cloud.ovh.net/v1
LLM_PROFILES__OLALA__API_KEY=<ta clé>
LLM_PROFILES__OLALA__REASONING_EFFORT=low
LLM_PROFILES__OLALA__PRICE_INPUT=0
LLM_PROFILES__OLALA__PRICE_OUTPUT=0"""


def render() -> None:
    user = require_admin()
    st.markdown("## Paramètres")
    with st.container(border=True):
        _llm(user.id)
    with st.container(border=True):
        _token_limit(user.id)


# --------------------------------------------------------------------------- #


def _describe(p: LLMProfile) -> str:
    if p.provider == "ollama":
        parts = [f"Ollama · `{p.model}` · {p.base_url} · contexte {p.num_ctx}"]
    else:
        parts = [f"API compatible OpenAI · `{p.model}` · {p.base_url}"]
        parts.append("clé API ✓" if p.api_key else ":orange[sans clé API]")
        if p.reasoning_effort:
            parts.append(f"raisonnement {p.reasoning_effort}")
        if p.json_mode == "json_object":
            parts.append("json_object")
    if p.price_input is not None or p.price_output is not None:
        currency = get_settings().llm_price_currency
        prices = f"{p.price_input or 0:g} / {p.price_output or 0:g}".replace(".", ",")
        parts.append(f"{prices} {currency} par M tokens")
    if p.name == "default":
        declared = "default" in {k.lower() for k in get_settings().llm_profiles}
        parts.append("`LLM_PROFILES__DEFAULT__*`" if declared else "variables `LLM_*`")
    return " · ".join(parts)


def _test(p: LLMProfile) -> Check:
    """Ollama : modèle installé ; API distante : modèle proposé, puis appel réel d'un token (clé, quota)."""
    if p.provider == "ollama":
        base = p.base_url
        try:
            tags = httpx.get(f"{base}/api/tags", timeout=5).raise_for_status().json().get("models", [])
        except Exception as exc:
            return Check("Ollama", False, f"Injoignable ({type(exc).__name__}) · {base}")
        names = {m["name"] for m in tags}
        if p.model in names or f"{p.model}:latest" in names:
            return Check("Ollama", True, f"{p.model} installé")
        return Check("Ollama", False, f"Modèle {p.model} absent : `ollama pull {p.model}`")
    check = check_remote_llm(p)
    if check is None or not check.ok:
        return check or Check("LLM distant", False, "Non vérifiable")
    from getiajob.llm import get_chat_model

    try:
        get_chat_model(p).bind(max_tokens=1).invoke("ok")
    except Exception as exc:
        return Check("LLM distant", False, f"Appel de test refusé : {type(exc).__name__} : {exc}"[:400])
    return Check("LLM distant", True, f"{check.detail} · appel de test accepté (clé et quota OK)")


def _llm(actor_id: int) -> None:
    st.markdown("#### Modèle de langage (LLM)")
    profiles = llm_profiles()
    active = llm_settings.current()
    names = list(profiles)
    choice = st.radio(
        "LLM utilisé pour l'analyse des CV, l'évaluation des offres et la lecture des alertes e-mail",
        names,
        index=names.index(active.name) if active.name in names else 0,
        format_func=lambda k: profiles[k].title + ("  :green-badge[actif]" if k == active.name else ""),
        captions=[_describe(p) for p in profiles.values()],
        key="settings_llm",
    )
    selected = profiles[choice]
    with st.container(horizontal=True, gap="small"):
        if st.button(
            "Utiliser ce LLM",
            type="primary",
            icon=":material/check:",
            disabled=choice == active.name or is_read_only(),
        ):
            llm_settings.set_profile(actor_id, choice)
            st.session_state.pop(TEST_KEY, None)
            st.toast(f"LLM actif : {selected.title}", icon=":material/check_circle:")
            st.rerun()
        if st.button("Tester", icon=":material/network_check:", help="Vérifie la configuration sélectionnée"):
            with st.spinner("Test en cours…"):
                st.session_state[TEST_KEY] = (choice, _test(selected))
    if (test := st.session_state.get(TEST_KEY)) and test[0] == choice:
        check = test[1]
        (st.success if check.ok else st.error)(f"**{check.label}** — {check.detail}")
    st.caption(
        "Pris en compte immédiatement, sans redémarrage, y compris par les tâches planifiées. Les embeddings "
        f"(similarité) restent sur Ollama (`{get_settings().embedding_model}`). Changer de LLM fait "
        "réévaluer les offres déjà notées (la note dépend du modèle)."
    )
    with st.expander("Ajouter une configuration"):
        st.markdown(
            "Les configurations se déclarent dans le `.env` (les clés API n'apparaissent donc jamais dans "
            "l'interface), puis `scripts/restart.sh`. Champs : `LABEL`, `PROVIDER` (`ollama` ou `openai`), "
            "`MODEL`, `BASE_URL`, `API_KEY`, `JSON_MODE`, `REASONING_EFFORT`, `NUM_CTX` (Ollama), "
            "`PRICE_INPUT`, `PRICE_OUTPUT` ; bloc `DEFAULT` seulement : `EMBEDDING_MODEL`, `EMBEDDING_DIM`."
        )
        st.code(ENV_EXAMPLE, language="bash")


# --------------------------------------------------------------------------- #


def _token_limit(actor_id: int) -> None:
    st.markdown("#### Limite de tokens")
    limit = llm_settings.token_limit()
    b = llm_settings.budget()
    if limit.enabled:
        start = llm_settings.period_start(limit.period)
        since = f" depuis le {local(start)}" if start else " depuis le début"
        st.progress(
            min(1.0, b.used / limit.tokens),
            text=f"{tokens_label(b.used)} / {tokens_label(limit.tokens)} tokens utilisés{since}"
            + (" — **limite atteinte**" if b.used >= limit.tokens else ""),
        )
        usage = llm_settings.period_usage(limit)
        at_limit = llm_settings.cost_at_limit(limit, usage)
        if at_limit is None:
            st.caption(
                ":material/euro: Coût non calculé : aucun prix renseigné pour le LLM actif "
                "(`PRICE_INPUT` / `PRICE_OUTPUT` de sa configuration)."
            )
        else:
            spent = usage.cost or 0.0
            st.progress(
                min(1.0, spent / at_limit) if at_limit > 0 else 0.0,
                text=f"{money_label(spent)} consommés / ~{money_label(at_limit)} estimés à la limite"
                + (" (LLM actif gratuit)" if at_limit == spent else ""),
            )
            st.caption(
                "Coût estimé à la limite : coût déjà consommé sur la période + tokens restants aux prix du "
                "LLM actif, répartis entre envoyés et générés comme jusqu'ici."
            )
        if not b.applies:
            st.caption(
                ":material/info: Le LLM actif est Ollama et la limite ne compte pas Ollama : pas de blocage."
            )
    enabled = st.toggle("Limiter la consommation de tokens", value=limit.enabled, key="limit_on")
    c1, c2 = st.columns(2)
    tokens = c1.number_input(
        "Nombre maximum de tokens (envoyés + générés, tous utilisateurs)",
        min_value=1_000,
        step=100_000,
        value=limit.tokens or 1_000_000,
        disabled=not enabled,
        key="limit_tokens",
    )
    period = c2.selectbox(
        "Période",
        list(PERIODS),
        index=list(PERIODS).index(limit.period),
        format_func=lambda k: PERIODS[k].capitalize(),
        disabled=not enabled,
        key="limit_period",
        help="Jour et mois civils, dans le fuseau d'affichage (DISPLAY_TIMEZONE).",
    )
    include_local = st.checkbox(
        "Compter aussi Ollama (local, gratuit)",
        value=limit.include_local,
        disabled=not enabled,
        key="limit_local",
        help="Sinon seuls les LLM distants (API payantes) sont comptés et bloqués.",
    )
    new = TokenLimit(int(tokens), period, include_local) if enabled else TokenLimit()
    if st.button("Enregistrer la limite", icon=":material/save:", disabled=new == limit or is_read_only()):
        llm_settings.set_token_limit(actor_id, new)
        st.toast("Limite enregistrée", icon=":material/check_circle:")
        st.rerun()
    st.caption(
        "Une fois la limite atteinte, chaque appel au LLM est refusé : l'analyse d'un CV ou la recherche en "
        "cours s'arrête avec un message explicite. L'appel en cours au moment du dépassement se termine, la "
        "consommation peut donc dépasser légèrement la limite. Le budget restant est affiché avant chaque "
        "analyse et chaque évaluation."
    )
