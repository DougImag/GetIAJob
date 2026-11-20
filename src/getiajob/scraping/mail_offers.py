"""Offres d'un e-mail d'alerte d'un site d'emploi quelconque (Free-Work, Indeed, Welcome to the Jungle…).

Contrairement à l'APEC (format connu, extraction sans IA), chaque site met ses offres en page à sa
façon : le texte du mail, liens numérotés, est confié au LLM qui renvoie intitulé, entreprise, lieu,
contrat, salaire et numéro du lien de chaque offre. Les liens de suivi (Mailjet, SendGrid…) sont
décodés pour retrouver l'adresse réelle de l'annonce, sans appel réseau.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import re
from datetime import datetime
from email.message import Message
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from getiajob.schemas import MailOffers
from getiajob.scraping.base import JobPosting

log = logging.getLogger(__name__)

SOURCE = "mail"
MAX_TEXT = 12_000  # caractères du mail transmis au LLM
REDIRECT_PARAMS = (
    "url",
    "u",
    "redirect",
    "redirect_url",
    "redirect_uri",
    "target",
    "dest",
    "destination",
    "link",
)
TRACKING_PARAM_RE = re.compile(r"^(utm_|mc_|trk|_hs|mkt_|ref$|source$|origin$)", re.I)
SKIP_LINK_RE = re.compile(
    r"(unsubscribe|desinscri|désinscri|desabonn|désabonn|preferences|privacy|confidentialit|mailto:|tel:)",
    re.I,
)
PLAIN_LINK_RE = re.compile(r"<(https?://[^>\s]+)>")

PROMPT = """Tu extrais les offres d'emploi ou de mission d'un e-mail d'alerte envoyé par un site d'emploi.
Les liens du mail sont numérotés [L1], [L2]… juste après leur texte.
- Une entrée par offre réellement présente dans le mail ; n'invente rien, ne complète rien.
- Ignore publicités, articles, conseils, boutons génériques (« Voir toutes les offres », « Lancer le
  matching »), liens de désinscription.
- title : intitulé exact ; company, location, contract, salary : seulement s'ils sont écrits pour cette offre.
- link : numéro N du lien [LN] qui ouvre cette offre (son titre ou son bouton « Postuler » / « Voir »).
- Aucune offre dans le mail : liste vide."""


def _b64_url(segment: str) -> str | None:
    if len(segment) < 16:
        return None
    try:
        decoded = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return decoded if decoded.startswith(("http://", "https://")) else None


def clean_url(url: str) -> str:
    """Retire les paramètres de suivi (utm_…) d'une adresse."""
    parts = urlparse(url)
    query = [(k, v) for k, vs in parse_qs(parts.query, keep_blank_values=True).items() for v in vs]
    kept = [(k, v) for k, v in query if not TRACKING_PARAM_RE.match(k)]
    return urlunparse(parts._replace(query=urlencode(kept), fragment=""))


def resolve_link(url: str) -> str:
    """Adresse réelle derrière un lien de suivi : paramètre `url=`, ou segment du chemin en base64."""
    for _ in range(3):
        try:
            parts = urlparse(url)
        except ValueError:
            break
        query = parse_qs(parts.query)
        target = next(
            (v for k in REDIRECT_PARAMS for v in query.get(k, []) if v.startswith(("http://", "https://"))),
            None,
        )
        if target is None:
            target = next((t for seg in reversed(parts.path.split("/")) if (t := _b64_url(seg))), None)
        if target is None:
            break
        url = target
    return clean_url(url)


def numbered_text(msg: Message) -> tuple[str, list[str]]:
    """Texte du mail avec ses liens numérotés « [LN] », et la liste des adresses (résolues) des liens."""
    html = plain = None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain") or part.get_filename():
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if ctype == "text/html" and html is None:
            html = text
        elif ctype == "text/plain" and plain is None:
            plain = text
    links: list[str] = []

    def number(url: str) -> str | None:
        if SKIP_LINK_RE.search(url):
            return None
        resolved = resolve_link(url)
        if SKIP_LINK_RE.search(resolved):
            return None
        if resolved not in links:
            links.append(resolved)
        return f"[L{links.index(resolved) + 1}]"

    if html:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["style", "script", "head"]):
            tag.decompose()
        for a in soup.find_all("a", href=True):
            ref = number(str(a["href"]))
            label = a.get_text(" ", strip=True)
            a.replace_with(f" {label} {ref} " if ref and label else f" {label} ")
        text = soup.get_text("\n", strip=True)
    else:
        text = PLAIN_LINK_RE.sub(lambda m: number(m.group(1)) or "", plain or "")
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text[:MAX_TEXT], links


def _one_line(value: str | None, sep: str = " ") -> str | None:
    lines = [" ".join(line.split()) for line in (value or "").splitlines()]
    return sep.join(x for x in lines if x) or None


def offer_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def extract_offers(
    msg: Message, label: str, posted_at: datetime | None, subject: str = ""
) -> list[JobPosting]:
    """Offres d'un mail d'alerte, extraites par le LLM (liste vide si le mail n'en contient pas)."""
    from getiajob.llm import structured
    from getiajob.services.monitoring import timed

    text, links = numbered_text(msg)
    if not links:
        return []
    with timed("llm", "alerte_mail"):
        result: MailOffers = structured(MailOffers).invoke(
            [("system", PROMPT), ("human", f"Expéditeur : {label}\nObjet : {subject}\n\n{text}")]
        )
    postings: list[JobPosting] = []
    seen: set[str] = set()
    for o in result.offers:
        title = " ".join((o.title or "").split())
        if not title or o.link is None or not 1 <= o.link <= len(links):
            continue
        url = links[o.link - 1]
        if url in seen:
            continue
        seen.add(url)
        # Le site émetteur n'est pas l'employeur (« Free-Work » n'est pas l'entreprise de l'offre)
        company = _one_line(o.company)
        if company and company.lower() in (label.lower(), label.lower().replace("-", " ")):
            company = None
        salary = _one_line(o.salary, " · ")
        criteria = {"Origine": f"Alerte e-mail {label}"}
        if o.contract:
            criteria["Contrat"] = o.contract
        details = " — ".join(x for x in (o.contract, o.location, salary) if x)
        description = "\n".join(x for x in (title, company, details) if x) + (
            f"\n\nOffre reçue par alerte e-mail {label}. Description complète sur le site de l'annonce."
        )
        postings.append(
            JobPosting(
                source=SOURCE,
                source_id=offer_id(url),
                title=title,
                url=url,
                company=company,
                location=o.location or None,
                posted_at=posted_at,
                description=description,
                salary=salary,
                criteria=criteria,
            )
        )
    log.info("Alerte %s : %d offre(s) extraite(s) par l'IA.", label, len(postings))
    return postings
