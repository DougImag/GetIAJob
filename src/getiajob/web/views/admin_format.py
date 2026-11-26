"""Petits utilitaires d'affichage partagés par les pages de supervision (et les estimations de coût)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from getiajob.config import get_settings

# Couleurs d'état (réservées : jamais réutilisées pour une série quelconque)
STATUS_COLORS = {"Terminée": "#16a34a", "Échec": "#dc2626", "En cours": "#94a3b8"}


def _zone():
    try:
        return ZoneInfo(get_settings().display_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def local(when: datetime | None) -> str:
    """Date affichée dans le fuseau de l'administrateur (DISPLAY_TIMEZONE, Europe/Paris par défaut)."""
    if when is None:
        return "–"
    zone = _zone()
    return f"{when.astimezone(zone) if zone else when:%d/%m/%Y %H:%M}"


def tokens_label(n: int | float | None) -> str:
    """12 345 678 → « 12,3 M » ; 45 678 → « 45,7 k » ; 3 550 → « 3 550 »."""
    n = int(n or 0)
    if n >= 999_950:  # 999 950 s'afficherait « 1000,0 k »
        return f"{n / 1_000_000:.1f} M".replace(".", ",")
    if n >= 10_000:
        return f"{n / 1_000:.1f} k".replace(".", ",")
    return f"{n:,}".replace(",", "\u202f")


def money_label(value: float | None) -> str:
    if value is None:
        return "–"
    currency = get_settings().llm_price_currency
    amount = f"{value:.2f}" if value >= 0.1 or value == 0 else f"{value:.4f}"
    return f"{amount.replace('.', ',')} {currency}"


def estimate_label(est) -> str:
    """« ~22 k tokens (~16 k envoyés + ~6 k générés) · ~0,02 € » pour une `services.estimate.Estimate`."""
    label = (
        f"~{tokens_label(est.total)} tokens "
        f"(~{tokens_label(est.input_tokens)} envoyés + ~{tokens_label(est.output_tokens)} générés)"
    )
    if est.cost is not None:
        label += f" · ~{money_label(est.cost)}"
    return label


def estimate_note(est) -> str:
    """Précision ajoutée quand l'estimation n'a pas encore d'historique pour le modèle configuré."""
    if est.measured:
        return ""
    from getiajob.services.llm_settings import current

    reasoning = ", raisonnement non compté" if current().provider == "openai" else ""
    return f" — valeurs par défaut (pas encore de mesure pour ce modèle{reasoning})"


def budget_status(needed: int, user_id: int | None = None) -> tuple[str | None, str]:
    """Message sur la limite de tokens pour un traitement estimé à `needed` tokens, et son niveau :
    « ok » (assez de budget), « warn » (budget insuffisant : arrêt en cours de route), « block » (épuisé).

    Tient compte de la limite globale et de la limite mensuelle de `user_id` : la plus contraignante."""
    from getiajob.services.llm_settings import budget, user_budget

    limits = []  # (restant, libellé, où la relever)
    b = budget()
    if b.remaining is not None:
        limits.append((b.remaining, f"limite globale de {b.limit.label}", "Admin › Paramètres"))
    if (ub := user_budget(user_id)) is not None:
        limits.append((ub.remaining, f"ta limite de {ub.label}", "Admin › Utilisateurs"))
    if not limits:
        return None, "ok"
    remaining, label, where = min(limits)
    if remaining == 0:
        return (
            f"{label[0].upper()}{label[1:]} atteinte : l'IA ne peut plus être appelée "
            f"(un administrateur peut la relever dans {where}).",
            "block",
        )
    if needed > remaining:
        return (
            f"Budget restant : {tokens_label(remaining)} tokens ({label}), moins que l'estimation "
            f"(~{tokens_label(needed)}) : le traitement s'arrêtera une fois la limite atteinte.",
            "warn",
        )
    return f"Budget restant : {tokens_label(remaining)} tokens ({label}).", "ok"


def show_budget(msg: str, level: str) -> None:
    """Affiche le message de `budget_status` (st.caption n'accepte pas d'icône : elle est dans le texte)."""
    import streamlit as st

    if level == "ok":
        st.caption(f":material/data_usage: {msg}")
    else:
        (st.error if level == "block" else st.warning)(msg, icon=":material/data_usage:")


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min".replace(".", ",")
    return f"{seconds / 3600:.1f} h".replace(".", ",")


def pivot(rows: list[dict], index: str, columns: str, values: str, order: list[str]) -> pd.DataFrame:
    """Lignes (jour, catégorie, n) → tableau large, colonnes dans un ordre fixe, jours manquants à 0."""
    df = pd.DataFrame(rows).pivot_table(index=index, columns=columns, values=values, aggfunc="sum")
    df = df.reindex(columns=order).fillna(0)
    df.index = pd.to_datetime(df.index)
    full = pd.date_range(df.index.min(), df.index.max(), freq="D")
    return by_day(df.reindex(full, fill_value=0))


def by_day(df: pd.DataFrame) -> pd.DataFrame:
    """Index de dates → libellés « mm-jj », triables (barres larges et lisibles plutôt qu'un axe temporel)."""
    df = df.copy()
    df.index = pd.Index([f"{d:%m-%d}" for d in pd.to_datetime(df.index)], name="Jour")
    return df
