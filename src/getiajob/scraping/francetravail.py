"""Source France Travail : API officielle « Offres d'emploi v2 » (francetravail.io).

Nécessite une application déclarée sur https://francetravail.io (identifiant + secret client,
API « Offres d'emploi v2 » activée). Gratuit, sans scraping.
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

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
API_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2"
OFFER_URL = "https://candidat.francetravail.fr/offres/recherche/detail/{id}"
SCOPE = "api_offresdemploiv2 o2dsoffre"
PAGE_SIZE = 150  # maximum autorisé par l'API
MAX_INDEX = 3149  # au-delà, l'API refuse la pagination
PUBLISHED_SINCE = (1, 3, 7, 14, 31)  # seules valeurs acceptées pour `publieeDepuis`
NATIONWIDE = {"", "france", "toute la france", "france entiere"}
# Grandes villes à arrondissements : on interroge le département (Paris) ou la commune + rayon.
CITY_DEPARTMENTS = {"paris": "75"}

CRITERIA_FIELDS = {
    "Contrat": "typeContratLibelle",
    "Durée du travail": "dureeTravailLibelle",
    "Expérience": "experienceLibelle",
    "Qualification": "qualificationLibelle",
    "Secteur": "secteurActiviteLibelle",
}


def published_since(days: int) -> int:
    """Plus petite valeur acceptée par l'API couvrant `days` jours."""
    return next((d for d in PUBLISHED_SINCE if d >= days), PUBLISHED_SINCE[-1])


def parse_offer(item: dict) -> JobPosting:
    criteria = {label: str(item[key]) for label, key in CRITERIA_FIELDS.items() if item.get(key)}
    origin = item.get("origineOffre") or {}
    partners = [p.get("nom") for p in origin.get("partenaires") or [] if p.get("nom")]
    if partners:
        criteria["Diffusée par"] = ", ".join(partners)
    skills = [c.get("libelle") for c in item.get("competences") or [] if c.get("libelle")]
    description = clean_html(item.get("description")) or ""
    if skills:
        description += "\n\nCompétences : " + ", ".join(skills)
    return JobPosting(
        source="francetravail",
        source_id=str(item["id"]),
        title=clean_html(item.get("intitule")) or "Offre sans titre",
        url=origin.get("urlOrigine") or OFFER_URL.format(id=item["id"]),
        company=((item.get("entreprise") or {}).get("nom") or "").strip() or None,
        location=(item.get("lieuTravail") or {}).get("libelle"),
        posted_at=parse_datetime(item.get("dateCreation")),
        description=description.strip() or None,
        salary=(item.get("salaire") or {}).get("libelle"),
        criteria=criteria,
    )


class FranceTravailAuthError(RuntimeError):
    """Identifiants d'API refusés (message explicite pour l'administrateur)."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {429, 500, 502, 503}


class FranceTravailSource:
    name = "francetravail"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        delay: float = 0.3,
        client: httpx.Client | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.delay = delay
        self._client = client or httpx.Client(timeout=30, event_hooks=http_hooks("francetravail"))
        self._token: str | None = None
        self._token_expiry = 0.0
        self._places: dict[str, dict[str, str]] | None = None

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ auth
    def _auth_header(self) -> dict[str, str]:
        if not self._token or time.monotonic() > self._token_expiry:
            response = self._client.post(
                TOKEN_URL,
                params={"realm": "/partenaire"},
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": SCOPE,
                },
            )
            if response.status_code in (400, 401):
                try:
                    error = response.json().get("error") or response.text[:100]
                except ValueError:
                    error = response.text[:100]
                raise FranceTravailAuthError(
                    f"identifiants refusés par France Travail ({error}) : vérifier "
                    "FRANCETRAVAIL_CLIENT_ID / FRANCETRAVAIL_CLIENT_SECRET et que l'application "
                    "est bien abonnée à l'API « Offres d'emploi v2 »"
                )
            response.raise_for_status()
            payload = response.json()
            self._token = payload["access_token"]
            self._token_expiry = time.monotonic() + int(payload.get("expires_in", 1500)) - 60
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        time.sleep(self.delay)  # l'API limite le nombre d'appels par seconde
        response = self._client.get(f"{API_URL}{path}", params=params, headers=self._auth_header())
        if response.status_code >= 400 and response.status_code != 400:
            response.raise_for_status()
        return response

    # ------------------------------------------------------------- lieux
    def _referentials(self) -> dict[str, dict[str, str]]:
        """Noms normalisés → paramètre de recherche (département, région, commune)."""
        if self._places is None:
            places: dict[str, dict[str, str]] = {}
            kinds = (("communes", "commune"), ("regions", "region"), ("departements", "departement"))
            for kind, param in kinds:
                try:
                    items = self._get(f"/referentiel/{kind}").json()
                except Exception as exc:
                    log.warning("Référentiel France Travail %s indisponible : %s", kind, exc)
                    continue
                for item in items:
                    # Les départements écrasent les communes homonymes (ex. « Paris »).
                    places[normalize(item.get("libelle"))] = {param: str(item["code"])}
                    if kind == "communes":
                        dep = {"departement": item.get("codeDepartement", "")}
                        places.setdefault(f"#{item['code']}", dep)
            self._places = places
        return self._places

    def location_params(self, location: str) -> dict[str, str] | None:
        """Paramètres de lieu pour l'API ; {} = toute la France, None = lieu inconnu."""
        key = normalize(location)
        if key in NATIONWIDE:
            return {}
        if key in CITY_DEPARTMENTS:
            return {"departement": CITY_DEPARTMENTS[key]}
        if key.isdigit() and len(key) in (2, 3) or key in ("2a", "2b"):
            return {"departement": key.upper()}
        if key.isdigit() and len(key) == 5:  # code postal → département
            return {"departement": key[:3] if key.startswith("97") else key[:2]}
        found = self._referentials().get(key)
        if found and "commune" in found:
            return {**found, "distance": "10"}
        return found

    # ------------------------------------------------------------ recherche
    def search(
        self, keywords: str, location: str, *, days: int = 7, limit: int = 50, **_filters
    ) -> Iterator[JobPosting]:
        where = self.location_params(location)
        if where is None:
            log.warning("France Travail : lieu « %s » non reconnu, recherche sur toute la France.", location)
            where = {}
        params = {"motsCles": keywords, "publieeDepuis": published_since(days), "sort": 1, **where}

        start, yielded = 0, 0
        while yielded < limit and start <= MAX_INDEX:
            end = min(start + min(PAGE_SIZE, limit - yielded), MAX_INDEX + 1) - 1
            response = self._get("/offres/search", {**params, "range": f"{start}-{end}"})
            if response.status_code == 400 and "commune" in params:
                # Commune refusée (ex. ville à arrondissements) : repli sur son département.
                dep = self._referentials().get(f"#{params.pop('commune')}", {}).get("departement")
                params.pop("distance", None)
                if dep:
                    params["departement"] = dep
                continue
            if response.status_code == 400:
                log.warning("France Travail a refusé la recherche « %s » : %s", keywords, response.text[:200])
                return
            if response.status_code == 204 or not response.content:
                return
            results = response.json().get("resultats") or []
            for item in results:
                yield parse_offer(item)
                yielded += 1
                if yielded >= limit:
                    return
            if len(results) < end - start + 1:
                return
            start = end + 1

    def details(self, posting: JobPosting) -> JobPosting:
        return posting  # la recherche renvoie déjà la description complète
