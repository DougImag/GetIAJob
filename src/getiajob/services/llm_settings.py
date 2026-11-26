"""Réglages du LLM choisis dans Admin › Paramètres : configuration active et limite de tokens.

Les configurations sont déclarées dans le .env (cf. `config.LLMProfile`) ; le choix de l'administrateur
et la limite sont enregistrés en base (table `app_settings`), donc partagés par l'interface et les
tâches planifiées, et pris en compte sans redémarrage (relus au plus toutes les `CACHE_SECONDS`).
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text

from getiajob.config import DEFAULT_PROFILE, LLMProfile, get_settings, llm_profiles
from getiajob.db import AppSetting, User, get_engine, session_scope
from getiajob.services.monitoring import audit

log = logging.getLogger(__name__)

CACHE_SECONDS = 10
PROFILE_KEY = "llm_profile"
LIMIT_KEY = "token_limit"
PERIODS = {"day": "par jour", "month": "par mois", "total": "au total"}

_cache: dict[str, tuple[float, Any]] = {}


class TokenBudgetExceeded(RuntimeError):
    """Limite de tokens atteinte : l'appel au LLM est refusé."""


def _read(key: str) -> dict | None:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        with session_scope() as session:
            value = session.scalar(select(AppSetting.value).where(AppSetting.key == key))
    except Exception as exc:  # base indisponible, table absente : réglages par défaut
        log.debug("Réglage %s illisible : %s", key, exc)
        value = None
    _cache[key] = (time.monotonic(), value)
    return value


def _write(actor_id: int | None, key: str, value: dict) -> None:
    with session_scope() as session:
        row = session.get(AppSetting, key) or AppSetting(key=key)
        row.value, row.updated_by = value, actor_id
        session.add(row)
    _cache.pop(key, None)


# --------------------------------------------------------------------------- #
# Configuration du LLM
# --------------------------------------------------------------------------- #


def selected_name() -> str:
    value = _read(PROFILE_KEY) or {}
    return str(value.get("name") or DEFAULT_PROFILE)


def current() -> LLMProfile:
    """Configuration de LLM active : celle choisie par l'administrateur, sinon « default » (LLM_*).

    Une configuration choisie puis retirée du .env retombe sur « default ».
    """
    profiles = llm_profiles(get_settings())
    return profiles.get(selected_name()) or profiles[DEFAULT_PROFILE]


def set_profile(actor_id: int | None, name: str) -> None:
    profiles = llm_profiles(get_settings())
    if name not in profiles:
        raise ValueError(f"Configuration de LLM inconnue : {name}")
    previous = current()
    _write(actor_id, PROFILE_KEY, {"name": name})
    audit(actor_id, "settings.llm_profile", profiles[name].title, f"Avant : {previous.title}")


# --------------------------------------------------------------------------- #
# Limite de tokens
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TokenLimit:
    tokens: int | None = None  # None : pas de limite
    period: str = "month"  # day, month, total
    include_local: bool = False  # compter aussi Ollama (gratuit) ?

    @property
    def enabled(self) -> bool:
        return bool(self.tokens)

    @property
    def label(self) -> str:
        return f"{self.tokens:,} tokens {PERIODS.get(self.period, '')}".replace(",", " ")


def token_limit() -> TokenLimit:
    value = _read(LIMIT_KEY) or {}
    try:
        return TokenLimit(
            tokens=int(value["tokens"]) if value.get("tokens") else None,
            period=value.get("period", "month") if value.get("period") in PERIODS else "month",
            include_local=bool(value.get("include_local", False)),
        )
    except (TypeError, ValueError):
        return TokenLimit()


def set_token_limit(actor_id: int | None, limit: TokenLimit) -> None:
    _write(actor_id, LIMIT_KEY, asdict(limit))
    audit(
        actor_id,
        "settings.token_limit",
        limit.label if limit.enabled else "aucune limite",
        "Ollama compris" if limit.include_local else "LLM distants seulement",
    )


def period_start(period: str, now: datetime | None = None) -> datetime | None:
    """Début de la période en cours (minuit, 1er du mois, fuseau d'affichage) ; None : depuis toujours."""
    if period == "total":
        return None
    try:
        zone = ZoneInfo(get_settings().display_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC
    local = (now or datetime.now(UTC)).astimezone(zone)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        start = start.replace(day=1)
    return start


@dataclass(frozen=True)
class PeriodUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None  # None : aucun appel tarifé sur la période

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def period_usage(limit: TokenLimit) -> PeriodUsage:
    """Consommation sur la période de la limite (mêmes appels que ceux comptés par la limite)."""
    start = period_start(limit.period)
    conditions = ["true"]
    if start is not None:
        conditions.append("created_at >= :start")
    if not limit.include_local:
        conditions.append("provider <> 'ollama'")
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                text(
                    "SELECT coalesce(sum(input_tokens), 0) AS n_in, "
                    "coalesce(sum(output_tokens), 0) AS n_out, sum(cost) AS cost "
                    f"FROM llm_usage WHERE {' AND '.join(conditions)}"
                ),
                {"start": start},
            ).one()
    except Exception as exc:
        log.debug("Consommation illisible : %s", exc)
        return PeriodUsage()
    return PeriodUsage(int(row.n_in), int(row.n_out), float(row.cost) if row.cost is not None else None)


def used_tokens(limit: TokenLimit) -> int:
    """Tokens (envoyés + générés) consommés sur la période de la limite."""
    return period_usage(limit).tokens


DEFAULT_INPUT_SHARE = 0.85  # sans historique : part des tokens envoyés (mesurée sur les évaluations d'offres)


def cost_at_limit(limit: TokenLimit, usage: PeriodUsage) -> float | None:
    """Coût estimé une fois la limite atteinte : coût déjà consommé + tokens restants aux prix du LLM
    actif, répartis entre envoyés et générés comme sur la période (None si aucun prix n'est connu)."""
    if not limit.enabled:
        return None
    p = current()
    if p.price_input is None and p.price_output is None:
        return usage.cost
    share = usage.input_tokens / usage.tokens if usage.tokens else DEFAULT_INPUT_SHARE
    per_token = (share * (p.price_input or 0) + (1 - share) * (p.price_output or 0)) / 1e6
    return (usage.cost or 0) + max(0, limit.tokens - usage.tokens) * per_token


@dataclass(frozen=True)
class Budget:
    limit: TokenLimit
    used: int
    applies: bool  # la limite concerne-t-elle le LLM actif ? (Ollama non compté par défaut)

    @property
    def remaining(self) -> int | None:
        """Tokens encore disponibles ; None si aucune limite ne s'applique au LLM actif."""
        if not (self.limit.enabled and self.applies):
            return None
        return max(0, self.limit.tokens - self.used)

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0


def budget() -> Budget:
    limit = token_limit()
    applies = limit.enabled and (limit.include_local or current().provider != "ollama")
    return Budget(limit, used_tokens(limit) if limit.enabled else 0, applies)


# --------------------------------------------------------------------------- #
# Limite mensuelle par utilisateur (Admin › Utilisateurs)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UserBudget:
    limit: int  # tokens par mois
    used: int  # tokens consommés depuis le 1er du mois (tous LLM, Ollama compris)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    @property
    def label(self) -> str:
        return f"{self.limit:,} tokens par mois".replace(",", " ")


def user_limit(user_id: int) -> int | None:
    try:
        with session_scope() as session:
            value = session.scalar(select(User.token_limit).where(User.id == user_id))
    except Exception as exc:  # base indisponible : pas de limite
        log.debug("Limite de l'utilisateur %s illisible : %s", user_id, exc)
        return None
    return int(value) if value else None


def user_budget(user_id: int | None) -> UserBudget | None:
    """Budget mensuel propre à l'utilisateur ; None s'il n'a pas de limite."""
    if user_id is None or not (limit := user_limit(user_id)):
        return None
    from getiajob.services.usage import month_tokens

    return UserBudget(limit, month_tokens(user_id))


def check_budget() -> None:
    """Appelé avant chaque appel au LLM : refuse l'appel si la limite globale, ou la limite mensuelle de
    l'utilisateur pour qui l'appel est fait, est atteinte."""
    b = budget()
    if b.exhausted:
        raise TokenBudgetExceeded(
            f"Limite de {b.limit.label} atteinte ({b.used:,} utilisés) : appel au LLM refusé. "
            "Un administrateur peut la relever dans Admin › Paramètres.".replace(",", " ")
        )
    from getiajob.services.usage import current_user

    ub = user_budget(current_user())
    if ub is not None and ub.exhausted:
        raise TokenBudgetExceeded(
            f"Ta limite de {ub.label} est atteinte ({ub.used:,} utilisés) : appel au LLM refusé. "
            "Elle repart à zéro le 1er du mois ; un administrateur peut la relever dans "
            "Admin › Utilisateurs.".replace(",", " ")
        )
