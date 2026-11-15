"""Source Adzuna : API officielle (https://developer.adzuna.com), marché français.

Adzuna agrège les offres de nombreux sites d'emploi et cabinets. Clé gratuite (app_id + app_key),
quota journalier limité. L'API ne renvoie qu'un extrait de la description (≈ 500 caractères).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from getiajob.scraping.base import JobPosting, clean_html, normalize, parse_datetime
from getiajob.services.monitoring import http_hooks

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/fr/search/{page}"
PAGE_SIZE = 50  # maximum autorisé par l'API
MAX_PAGES = 10
MIN_ANNUAL_SALARY = 1000
NATIONWIDE = {"", "france", "toute la france", "france entiere"}
CONTRACT_LABELS = {"permanent": "CDI", "contract": "CDD / mission"}
TIME_LABELS = {"full_time": "Temps plein", "part_time": "Temps partiel"}


def _amount(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def format_salary(item: dict) -> str | None:
    # En dessous de 1 000 €, il s'agit d'un taux horaire ou journalier mal étiqueté : on l'ignore.
    low, high = (
        v if v and v >= MIN_ANNUAL_SALARY else None for v in (item.get("salary_min"), item.get("salary_max"))
    )
    if not low and not high:
        return None
    if low and high and round(low) != round(high):
        text = f"{_amount(low)} – {_amount(high)} € / an"
    else:
        text = f"{_amount(low or high)} € / an"
    if str(item.get("salary_is_predicted")) == "1":
        text += " (estimation Adzuna)"
    return text


def parse_offer(item: dict) -> JobPosting:
    criteria = {}
    if contract := CONTRACT_LABELS.get(item.get("contract_type") or ""):
        criteria["Contrat"] = contract
    if worktime := TIME_LABELS.get(item.get("contract_time") or ""):
        criteria["Durée du travail"] = worktime
    category = (item.get("category") or {}).get("label")
    if category and category.lower() not in ("unknown", "emplois autres/général"):
        criteria["Catégorie"] = category
    return JobPosting(
        source="adzuna",
        source_id=str(item["id"]),
        title=clean_html(item.get("title")) or "Offre sans titre",
        url=item.get("redirect_url") or f"https://www.adzuna.fr/details/{item['id']}",
        company=((item.get("company") or {}).get("display_name") or "").strip() or None,
        location=(item.get("location") or {}).get("display_name"),
        posted_at=parse_datetime(item.get("created")),
        description=clean_html(item.get("description")),
        salary=format_salary(item),
        criteria=criteria,
    )


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {500, 502, 503}


class AdzunaSource:
    name = "adzuna"

    def __init__(self, app_id: str, app_key: str, delay: float = 1.0, client: httpx.Client | None = None):
        self.app_id = app_id
        self.app_key = app_key
        self.delay = delay
        self._client = client or httpx.Client(timeout=30, event_hooks=http_hooks("adzuna"))

    def close(self) -> None:
        self._client.close()

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get(self, page: int, params: dict) -> dict:
        time.sleep(self.delay)
        response = self._client.get(
            SEARCH_URL.format(page=page),
            params={"app_id": self.app_id, "app_key": self.app_key, **params},
            headers={"Accept": "application/json"},
        )
        if response.status_code in (401, 403):
            raise PermissionError(
                f"clés refusées par Adzuna (HTTP {response.status_code}) : "
                "vérifier ADZUNA_APP_ID / ADZUNA_APP_KEY"
            )
        response.raise_for_status()
        return response.json()

    def search(
        self, keywords: str, location: str, *, days: int = 7, limit: int = 50, **_filters
    ) -> Iterator[JobPosting]:
        params: dict[str, str | int] = {
            "what": keywords,
            "max_days_old": days,
            "sort_by": "date",
            "results_per_page": min(PAGE_SIZE, limit),
        }
        if normalize(location) not in NATIONWIDE:
            params["where"] = location

        yielded = 0
        for page in range(1, MAX_PAGES + 1):
            results = self._get(page, params).get("results") or []
            for item in results:
                yield parse_offer(item)
                yielded += 1
                if yielded >= limit:
                    return
            if len(results) < int(params["results_per_page"]):
                return

    def details(self, posting: JobPosting) -> JobPosting:
        return posting  # l'API ne fournit que l'extrait ; la page complète n'est pas récupérée
