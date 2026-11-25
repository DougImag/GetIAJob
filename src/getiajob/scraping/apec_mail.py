"""Alertes e-mail APEC : lecture de la boîte (IMAP, lecture seule) et extraction des offres.

Les utilisateurs transfèrent leurs alertes APEC vers la boîte de GetIAJob. Chaque offre est
rattachée au compte dont l'adresse a envoyé (transféré) le mail. Aucun appel à apec.fr : les liens
de l'alerte encodent le numéro d'offre (paramètre `e` en base64), dont on déduit l'URL de l'annonce.
"""

from __future__ import annotations

import base64
import binascii
import email
import hashlib
import imaplib
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from getiajob.scraping.base import JobPosting

log = logging.getLogger(__name__)

OFFER_URL = "https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/{num}"
APEC_DOMAINS = ("apec.fr",)
OFFER_NUM_RE = re.compile(r"^\d{6,12}[A-Z]$")
LOCATION_RE = re.compile(r".+ - (\d{2,3}|2A|2B)$")
CONTRACT_RE = re.compile(r"^(CDI|CDD|Intérim|Interim|Stage|Alternance|Freelance|Indépendant)\b", re.I)
ALERT_DATE_RE = re.compile(r"\bdu (\d{2})/(\d{2})/(\d{4})")
SEARCH_RE = re.compile(r"correspondent? à votre recherche\s*:\s*([^\n|<]+)", re.I)
PLAIN_LINK_RE = re.compile(r"([^\n<>\t]*?)\s*<(https?://[^>\s]+)>")
MAX_MESSAGES = 300
FORWARD_HEADERS = ("X-Forwarded-For", "Resent-From", "X-Original-Sender")
# Bloc d'un transfert manuel (« Début du message réexpédié », « ---------- Forwarded message »…)
FORWARD_MARKER_RE = re.compile(
    r"(message (r[ée]exp[ée]di[ée]|transf[ée]r[ée]|d'origine)|forwarded message|original message"
    r"|weitergeleitete nachricht)",
    re.I,
)
FORWARDED_FROM_RE = re.compile(r"^[>\s*]*(?:De|From|Von|Exp[ée]diteur)\s*:\s*(.+?)\s*$", re.M | re.I)
# Domaines « composés » : le nom de l'émetteur est le 3e niveau (ex. pole-emploi.gouv.fr)
COMPOUND_TLDS = {"co", "com", "gouv", "org", "ac", "net"}


@dataclass
class ParsedAlert:
    message_key: str
    subject: str
    sender: str | None  # adresse de l'utilisateur qui a transféré l'alerte
    received_at: datetime | None
    verified: bool  # l'expéditeur est authentifié (SPF / DKIM / DMARC) par le serveur de réception
    postings: list[JobPosting] = field(default_factory=list)
    alert_name: str | None = None
    candidates: list[str] = field(default_factory=list)  # adresses pouvant être celle de l'utilisateur
    origin: str | None = None  # adresse du site émetteur de l'alerte (contact@free-work.com…)
    origin_name: str | None = None  # nom affiché de l'émetteur (« Free-Work »)
    message: Message | None = field(default=None, repr=False)

    @property
    def is_alert(self) -> bool:
        return bool(self.postings)

    @property
    def mail_type(self) -> str | None:
        """Type de mail : domaine du site émetteur (« apec.fr », « free-work.com »…)."""
        if self.postings:  # alerte APEC reconnue par son contenu, même transférée sans en-tête d'origine
            return "apec.fr"
        return mail_domain(self.origin) if self.origin else None


def message_key(msg: Message, raw: bytes) -> str:
    """Empreinte stable d'un e-mail (Message-ID, sinon contenu)."""
    basis = (msg.get("Message-ID") or "").strip().encode() or raw
    return hashlib.sha1(basis).hexdigest()


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _is_apec(address: str) -> bool:
    domain = address.rsplit("@", 1)[-1].lower()
    return any(domain == d or domain.endswith("." + d) for d in APEC_DOMAINS)


def sender_address(msg: Message) -> str | None:
    """Adresse de l'utilisateur à l'origine du mail.

    Transfert manuel : l'expéditeur (From). Transfert automatique (règle de messagerie) : le From
    reste celui de l'APEC, on prend alors l'adresse qui a transféré (X-Forwarded-For / Resent-From).
    """
    sender = parseaddr(msg.get("From") or "")[1].lower()
    if sender and not _is_apec(sender):
        return sender
    for header in ("X-Forwarded-For", "Resent-From", "X-Original-Sender"):
        for value in msg.get_all(header) or []:
            for candidate in re.split(r"[\s,]+", value):
                address = parseaddr(candidate)[1].lower()
                if "@" in address and not _is_apec(address):
                    return address
    return None


def address_candidates(msg: Message) -> list[str]:
    """Adresses pouvant être celle de l'utilisateur : expéditeur, puis adresses de transfert automatique."""
    found: list[str] = []
    for header in ("From", *FORWARD_HEADERS):
        for value in msg.get_all(header) or []:
            for candidate in re.split(r"[\s,]+", value) if header != "From" else [value]:
                address = parseaddr(candidate)[1].lower()
                if "@" in address and address not in found:
                    found.append(address)
    return found


def mail_domain(address: str) -> str:
    """Domaine « organisation » d'une adresse : news.free-work.com → free-work.com."""
    host = address.rsplit("@", 1)[-1].lower().strip(".")
    parts = host.split(".")
    keep = 3 if len(parts) > 2 and parts[-2] in COMPOUND_TLDS else 2
    return ".".join(parts[-keep:])


def origin_sender(msg: Message, body: str, users: set[str] = frozenset()) -> tuple[str | None, str | None]:
    """(adresse, nom) du site qui a émis l'alerte.

    Transfert automatique : l'expéditeur (From) est le site lui-même. Transfert manuel : l'expéditeur est
    l'utilisateur, le site est dans le bloc « De : … » du message transféré.
    """
    name, address = parseaddr(msg.get("From") or "")
    address = address.lower()
    if (
        address
        and address not in users
        and not re.match(r"^(fwd?|tr|wg)\s*:", _decode(msg.get("Subject")), re.I)
    ):
        return address, _decode(name).strip(" \"'") or None
    marker = FORWARD_MARKER_RE.search(body)
    for m in FORWARDED_FROM_RE.finditer(body, marker.start() if marker else 0):
        fname, faddr = parseaddr(m.group(1))
        if "@" in faddr and faddr.lower() not in users:
            return faddr.lower(), fname.strip(" \"'") or None
    return None, None


def is_verified(msg: Message) -> bool:
    """Le serveur de réception a-t-il authentifié l'expéditeur ? (1er en-tête = le plus récent)."""
    results = msg.get_all("Authentication-Results") or []
    if not results:
        return False
    return bool(re.search(r"\b(dkim|spf|dmarc)=pass\b", results[0], re.I))


def _offer_number(url: str) -> str | None:
    """Numéro d'offre APEC encodé dans un lien de l'alerte (paramètre `e` = base64 de `p1=…&p2=…`)."""
    try:
        query = parse_qs(urlparse(url).query)
    except ValueError:
        return None
    for param in ("e", "p2"):
        for value in query.get(param, []):
            if param == "p2":
                decoded = f"p2={value}"
            else:
                try:
                    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode(
                        "utf-8", "ignore"
                    )
                except (binascii.Error, ValueError):
                    continue
            num = (parse_qs(decoded).get("p2") or [""])[0].strip()
            if OFFER_NUM_RE.match(num):
                return num
    # Lien direct vers l'annonce (alerte non « trackée »)
    if m := re.search(r"detail-offre/(\d{6,12}[A-Z])", url):
        return m.group(1)
    return None


def _links(msg: Message) -> list[tuple[str, str]]:
    """Couples (texte, url) du mail, depuis la partie HTML (sinon texte brut)."""
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
    if html:
        soup = BeautifulSoup(html, "html.parser")
        return [(a.get_text(" ", strip=True), str(a["href"])) for a in soup.find_all("a", href=True)]
    if plain:
        return [(m.group(1).strip(), m.group(2)) for m in PLAIN_LINK_RE.finditer(plain)]
    return []


def _body_text(msg: Message) -> str:
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and not part.get_filename():
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", "replace")
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            html = payload.decode(part.get_content_charset() or "utf-8", "replace")
            return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    return ""


def _alert_date(subject: str, body: str, fallback: datetime | None) -> datetime | None:
    for text in (subject, body[:3000]):
        if m := ALERT_DATE_RE.search(text):
            try:
                return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), 8, tzinfo=UTC)
            except ValueError:
                pass
    return fallback


def parse_offers(msg: Message, alert_name: str | None, posted_at: datetime | None) -> list[JobPosting]:
    """Offres d'une alerte APEC : textes des liens regroupés par numéro d'offre, dans l'ordre."""
    grouped: dict[str, list[str]] = {}
    for text, url in _links(msg):
        num = _offer_number(url)
        if num is None:
            continue
        texts = grouped.setdefault(num, [])
        clean = " ".join(text.replace("\xa0", " ").split()).strip(" •")
        if clean and clean not in texts:
            texts.append(clean)

    postings = []
    for num, texts in grouped.items():
        if not texts:
            continue
        title, rest = texts[0], texts[1:]
        contract = next((t for t in rest if CONTRACT_RE.match(t)), None)
        location = next((t for t in rest if LOCATION_RE.match(t)), None)
        company = next((t for t in rest if t not in (contract, location)), None)
        criteria = {"Origine": "Alerte e-mail APEC"}
        if contract:
            criteria["Contrat"] = contract
        if alert_name:
            criteria["Alerte APEC"] = alert_name
        summary = [title, company, " — ".join(x for x in (contract, location) if x)]
        description = "\n".join(x for x in summary if x) + (
            f"\n\nOffre reçue par alerte e-mail APEC{f' (« {alert_name} »)' if alert_name else ''}. "
            "Description complète sur apec.fr."
        )
        postings.append(
            JobPosting(
                source="apec",
                source_id=num,
                title=title,
                url=OFFER_URL.format(num=num),
                company=company,
                location=location,
                posted_at=posted_at,
                description=description,
                criteria=criteria,
            )
        )
    return postings


def parse_alert(raw: bytes, users: set[str] = frozenset()) -> ParsedAlert:
    msg = email.message_from_bytes(raw)
    subject = _decode(msg.get("Subject"))
    try:
        received = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
    except (TypeError, ValueError):
        received = None
    body = _body_text(msg)
    alert_name = (m.group(1).strip() if (m := SEARCH_RE.search(body)) else None) or None
    origin, origin_name = origin_sender(msg, body, users)
    return ParsedAlert(
        message_key=message_key(msg, raw),
        subject=subject[:500],
        sender=sender_address(msg),
        received_at=received,
        verified=is_verified(msg),
        postings=parse_offers(msg, alert_name, _alert_date(subject, body, received)),
        alert_name=alert_name,
        candidates=address_candidates(msg),
        origin=origin,
        origin_name=origin_name,
        message=msg,
    )


# --------------------------------------------------------------------------- #
# Boîte IMAP
# --------------------------------------------------------------------------- #


class Mailbox:
    """Accès IMAP à la boîte des alertes.

    `readonly=True` : rien n'est modifié ni marqué comme lu. Sinon, `delete()` peut supprimer les
    mails traités : ils sont déplacés dans la corbeille de la boîte (vidée par le fournisseur, ex.
    30 jours chez Gmail) ; sans corbeille, ils sont marqués supprimés puis effacés.
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        port: int = 993,
        folder: str = "INBOX",
        readonly: bool = True,
    ):
        self.host, self.port, self.user, self.folder, self.readonly = host, port, user, folder, readonly
        # Les mots de passe d'application Google s'affichent avec des espaces : on les retire.
        self.password = password.replace(" ", "") if "gmail" in host or "googlemail" in host else password
        self._imap: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> Mailbox:
        self._imap = imaplib.IMAP4_SSL(self.host, self.port, timeout=30)
        try:
            self._imap.login(self.user, self.password)
        except imaplib.IMAP4.error as exc:
            raise PermissionError(
                f"connexion refusée par {self.host} ({exc}) : vérifier IMAP_USER / IMAP_PASSWORD "
                "(mot de passe d'application pour Gmail)"
            ) from exc
        status, _ = self._imap.select(self.folder, readonly=self.readonly)
        if status != "OK":
            raise RuntimeError(f"dossier IMAP « {self.folder} » introuvable")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._imap is not None:
            try:
                if not self.readonly:
                    self._imap.close()  # applique les suppressions en attente
                self._imap.logout()
            except Exception:
                pass

    def messages(
        self, since_days: int, known_keys: set[str] = frozenset()
    ) -> Iterator[tuple[bytes, str, bytes | None]]:
        """(uid, empreinte, contenu brut) des mails reçus depuis `since_days` jours.

        Contenu None pour un mail déjà traité (reconnu à son Message-ID) : seul l'uid est utile,
        pour pouvoir le supprimer.
        """
        assert self._imap is not None
        since = (date.today() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        status, data = self._imap.uid("SEARCH", None, "SINCE", since)
        if status != "OK" or not data or not data[0]:
            return
        for uid in data[0].split()[-MAX_MESSAGES:]:
            status, head = self._imap.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            if status != "OK" or not head or not isinstance(head[0], tuple):
                continue
            mid = (email.message_from_bytes(head[0][1]).get("Message-ID") or "").strip()
            if mid and (key := hashlib.sha1(mid.encode()).hexdigest()) in known_keys:
                yield uid, key, None
                continue
            status, body = self._imap.uid("FETCH", uid, "(BODY.PEEK[])")
            if status == "OK" and body and isinstance(body[0], tuple):
                raw = body[0][1]
                yield uid, message_key(email.message_from_bytes(raw), raw), raw

    def _trash_folder(self) -> str | None:
        """Dossier corbeille (attribut spécial \\Trash, ex. « [Gmail]/Corbeille »)."""
        assert self._imap is not None
        status, folders = self._imap.list()
        if status != "OK":
            return None
        for line in folders or []:
            text = line.decode(errors="ignore") if isinstance(line, bytes) else str(line)
            if "\\Trash" in text and (
                m := re.search(r'"([^"]+)"\s*$', text) or re.search(r"(\S+)\s*$", text)
            ):
                return m.group(1)
        return None

    def delete(self, uids: list[bytes]) -> int:
        """Supprime les mails (corbeille si elle existe). Renvoie le nombre de mails supprimés."""
        if not uids:
            return 0
        if self.readonly:
            raise RuntimeError("boîte ouverte en lecture seule")
        assert self._imap is not None
        uid_set = b",".join(uids).decode()
        trash = self._trash_folder()
        if trash and trash != self.folder:
            quoted = '"' + trash.replace('"', '\\"') + '"'
            status, _ = self._imap.uid("MOVE", uid_set, quoted)
            if status == "OK":
                return len(uids)
            status, _ = self._imap.uid("COPY", uid_set, quoted)
            if status != "OK":
                raise RuntimeError(f"copie vers « {trash} » impossible")
        self._imap.uid("STORE", uid_set, "+FLAGS.SILENT", "(\\Deleted)")
        self._imap.expunge()
        return len(uids)
