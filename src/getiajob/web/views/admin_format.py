"""Petits utilitaires d'affichage partagés par les pages de supervision."""

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


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"


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
