"""Scraper des offres LinkedIn via les pages publiques « guest » (aucun compte requis).

Attention : le scraping est contraire aux CGU de LinkedIn. Usage personnel, volume faible,
délais entre requêtes. La structure HTML peut changer à tout moment : les sélecteurs sont
regroupés ici pour être faciles à corriger.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup, Tag
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from getiajob.scraping.base import JobPosting
from getiajob.services.monitoring import http_hooks

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
JOB_VIEW_URL = "https://www.linkedin.com/jobs/view/{job_id}"

REMOTE_CODES = {"on-site": "1", "remote": "2", "hybrid": "3"}
EXPERIENCE_CODES = {
    "internship": "1",
    "entry": "2",
    "associate": "3",
    "mid-senior": "4",
    "director": "5",
    "executive": "6",
}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


@dataclass
class JobCard:
    linkedin_id: str
    title: str
    company: str | None
    location: str | None
    url: str
    posted_at: datetime | None


@dataclass
class JobDetails:
    description: str | None
    salary: str | None = None
    criteria: dict[str, str] = field(default_factory=dict)


def _text(el: Tag | None) -> str | None:
    if el is None:
        return None
    value = el.get_text(" ", strip=True)
    return value or None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_search_results(html: str) -> list[JobCard]:
    soup = BeautifulSoup(html, "html.parser")
    cards: dict[str, JobCard] = {}
    for card in soup.select("[data-entity-urn*='jobPosting']"):
        job_id = str(card["data-entity-urn"]).rsplit(":", 1)[-1]
        title = _text(card.select_one(".base-search-card__title"))
        if not job_id or not title or job_id in cards:
            continue
        link = card.select_one("a.base-card__full-link") or (card if card.name == "a" else None)
        href = link.get("href") if link else None
        url = str(href).split("?")[0] if href else JOB_VIEW_URL.format(job_id=job_id)
        time_el = card.select_one("time")
        cards[job_id] = JobCard(
            linkedin_id=job_id,
            title=title,
            company=_text(card.select_one(".base-search-card__subtitle")),
            location=_text(card.select_one(".job-search-card__location")),
            url=url,
            posted_at=_parse_date(str(time_el.get("datetime")) if time_el else None),
        )
    return list(cards.values())


def parse_job_details(html: str) -> JobDetails:
    soup = BeautifulSoup(html, "html.parser")
    desc_el = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    criteria: dict[str, str] = {}
    for item in soup.select("li.description__job-criteria-item"):
        key = _text(item.select_one(".description__job-criteria-subheader"))
        value = _text(item.select_one(".description__job-criteria-text"))
        if key and value:
            criteria[key] = value
    return JobDetails(
        description=desc_el.get_text("\n", strip=True) if desc_el else None,
        salary=_text(soup.select_one(".compensation__salary")),
        criteria=criteria,
    )


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {
        429,
        500,
        502,
        503,
        504,
    }


class LinkedInScraper:
    def __init__(self, delay: float = 2.5, client: httpx.Client | None = None) -> None:
        self.delay = delay
        self._client = client or httpx.Client(
            headers=HEADERS, timeout=30, follow_redirects=True, event_hooks=http_hooks("linkedin")
        )
        self._last_request = 0.0

    def __enter__(self) -> LinkedInScraper:
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def _throttle(self) -> None:
        wait = self.delay * random.uniform(0.7, 1.3) - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, url: str, params: dict | None = None) -> str:
        self._throttle()
        response = self._client.get(url, params=params)
        # LinkedIn renvoie 400/404 en fin de pagination ou pour une offre retirée.
        if response.status_code in (400, 404):
            return ""
        response.raise_for_status()
        return response.text

    def search(
        self,
        keywords: str,
        location: str,
        *,
        days: int = 7,
        remote: Iterable[str] = (),
        experience_levels: Iterable[str] = (),
        limit: int = 50,
    ) -> Iterator[JobCard]:
        params: dict[str, str | int] = {
            "keywords": keywords,
            "location": location,
            "f_TPR": f"r{days * 86400}",
        }
        if codes := [REMOTE_CODES[r] for r in remote if r in REMOTE_CODES]:
            params["f_WT"] = ",".join(codes)
        if codes := [EXPERIENCE_CODES[e] for e in experience_levels if e in EXPERIENCE_CODES]:
            params["f_E"] = ",".join(codes)

        yielded, start = 0, 0
        while yielded < limit:
            cards = parse_search_results(self._get(SEARCH_URL, {**params, "start": start}))
            if not cards:
                return
            for card in cards:
                yield card
                yielded += 1
                if yielded >= limit:
                    return
            start += len(cards)

    def fetch_details(self, linkedin_id: str) -> JobDetails:
        html = self._get(DETAIL_URL.format(job_id=linkedin_id))
        return parse_job_details(html) if html else JobDetails(description=None)


class LinkedInSource:
    """Adaptateur LinkedIn → interface commune des sources (`scraping.base.JobSource`)."""

    name = "linkedin"

    def __init__(self, delay: float = 2.5) -> None:
        self._scraper = LinkedInScraper(delay=delay)

    def close(self) -> None:
        self._scraper.__exit__()

    def search(
        self,
        keywords: str,
        location: str,
        *,
        days: int = 7,
        limit: int = 50,
        remote: Iterable[str] = (),
        experience_levels: Iterable[str] = (),
        **_filters,
    ) -> Iterator[JobPosting]:
        for card in self._scraper.search(
            keywords, location, days=days, remote=remote, experience_levels=experience_levels, limit=limit
        ):
            yield JobPosting(
                source="linkedin",
                source_id=card.linkedin_id,
                title=card.title,
                url=card.url,
                company=card.company,
                location=card.location,
                posted_at=card.posted_at,
            )

    def details(self, posting: JobPosting) -> JobPosting:
        details = self._scraper.fetch_details(posting.source_id)
        posting.description = details.description
        posting.salary = details.salary
        posting.criteria = details.criteria
        return posting
