"""CV au format PDF (A4), généré à partir du profil extrait.

Mise en page inspirée de data/cv/cv_modele.pdf, aux couleurs du site : bandeau en dégradé
indigo → violet (nom, titre, coordonnées), sections à filet dégradé, expériences sur une frise,
réalisations cochées en vert, compétences et technologies en pastilles. Polices standard du PDF
(Helvetica, ZapfDingbats pour les coches) : aucune police à installer.
"""

from __future__ import annotations

import re
import unicodedata
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from getiajob.schemas import CVProfile, Experience

# Charte du site (variables --gj-* de web/layout.py)
PRIMARY = HexColor("#4f46e5")  # --gj-primary
PRIMARY_2 = HexColor("#7c3aed")  # --gj-primary-2
HEAD = HexColor("#1e1b4b")  # --gj-head
TEXT = HexColor("#374151")  # --gj-text
MUTED = HexColor("#6b7280")  # --gj-muted
SURFACE_2 = HexColor("#f8f7ff")  # --gj-surface-2
LINE = HexColor("#eceafd")  # --gj-line
SOFT = HexColor("#e0e7ff")  # texte clair sur le bandeau
TAG_BG = HexColor("#e7e6fc")  # rgba(99, 102, 241, .12) sur fond blanc
TAG_NOW_BG = HexColor("#dcfce7")
GREEN = HexColor("#16a34a")
RAIL = HexColor("#c7d2fe")

PAGE_W, PAGE_H = A4
MARGIN = 15 * mm
BODY_W = PAGE_W - 2 * MARGIN
FONT, BOLD = "Helvetica", "Helvetica-Bold"
NO_PAD = {"leftPadding": 0, "rightPadding": 0, "topPadding": 0, "bottomPadding": 0}

STYLES = {
    "body": ParagraphStyle("body", fontName=FONT, fontSize=9.5, leading=13.5, textColor=TEXT),
    "title": ParagraphStyle("title", fontName=BOLD, fontSize=11, leading=14, textColor=HEAD),
    "date": ParagraphStyle(
        "date", fontName=BOLD, fontSize=8.5, leading=14, textColor=TEXT, alignment=TA_RIGHT
    ),
    "org": ParagraphStyle("org", fontName=FONT, fontSize=9.5, leading=13, textColor=MUTED),
    "desc": ParagraphStyle("desc", fontName=FONT, fontSize=9, leading=12.5, textColor=TEXT),
    "check": ParagraphStyle(
        "check",
        fontName=FONT,
        fontSize=9,
        leading=12.5,
        textColor=TEXT,
        leftIndent=11,
        bulletIndent=0,
        bulletFontName="ZapfDingbats",
        bulletFontSize=7.5,
        bulletColor=GREEN,
    ),
    "label": ParagraphStyle("label", fontName=BOLD, fontSize=9.5, leading=13, textColor=HEAD),
    "small": ParagraphStyle("small", fontName=FONT, fontSize=8.5, leading=11.5, textColor=MUTED),
}

# Caractères hors de l'encodage des polices standard (WinAnsi) → équivalent lisible
REPLACEMENTS = {
    "‐": "-",
    "‑": "-",
    "‒": "–",
    "―": "—",
    "−": "-",
    " ": " ",
    " ": " ",
    "→": "->",
    "←": "<-",
    "≤": "<=",
    "≥": ">=",
    "≈": "~",
    "✓": "",
    "✔": "",
    "●": "•",
    "▪": "•",
}
CURRENT = re.compile(r"^(pr[ée]sent|aujourd'hui|en cours|actuel(lement)?|now|current|today)$", re.I)
# Chiffres mis en valeur dans les réalisations (« 95% », « ~100 URLs », « 20+ »)
FIGURE = re.compile(r"(?<![\w.])([~>+]\d[\d.,]*(?:\s?(?:%|k€|K€|M€|€))?|\d[\d.,]*\s?(?:%|k€|K€|M€|€|\+))")
CHECK = "\u2714"  # ✔ (police ZapfDingbats)


def clean(value: str | None) -> str:
    """Texte compatible avec les polices standard du PDF."""
    text = unicodedata.normalize("NFC", value or "")
    text = "".join(REPLACEMENTS.get(c, c) for c in text)
    return text.encode("cp1252", "ignore").decode("cp1252").strip()


def esc(value: str | None) -> str:
    return escape(clean(value))


def emphasize(value: str) -> str:
    """Texte échappé pour un Paragraph, chiffres clés en gras."""
    parts = FIGURE.split(clean(value))
    return "".join(f"<b>{escape(p)}</b>" if i % 2 else escape(p) for i, p in enumerate(parts))


def is_current(end: str | None) -> bool:
    return bool(end and CURRENT.match(end.strip()))


def period(start: str | None, end: str | None) -> str:
    end = "Aujourd'hui" if is_current(end) else end
    if start and end and clean(start) == clean(end):
        return clean(start)
    return " – ".join(clean(d) for d in (start, end) if d and d.strip())


def initials(name: str | None) -> str:
    words = [w for w in re.split(r"[\s-]+", clean(name)) if w and w[0].isalpha()]
    return "".join(w[0] for w in words[:2]).upper() or "CV"


def file_name(p: CVProfile) -> str:
    base = unicodedata.normalize("NFKD", p.full_name or "").encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_")
    return f"CV_{base or 'GetIAJob'}.pdf"


def _gradient(c, x0, y0, x1, y1, colors=(PRIMARY, PRIMARY_2)) -> None:
    """Dégradé linéaire limité au chemin de découpe courant."""
    c.linearGradient(x0, y0, x1, y1, list(colors), extend=False)


# --------------------------------------------------------------------------- #
# Éléments graphiques
# --------------------------------------------------------------------------- #


class SectionTitle(Flowable):
    """Pastille dégradée, titre de section en capitales puis filet dégradé jusqu'à la marge."""

    def __init__(self, text: str):
        super().__init__()
        self.text = clean(text).upper()
        self.height = 24

    def wrap(self, avail_w, avail_h):
        self.width = avail_w
        return avail_w, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        path = c.beginPath()
        path.roundRect(0, 4, 4, 13, 2)
        c.clipPath(path, stroke=0, fill=0)
        _gradient(c, 0, 17, 0, 4)
        c.restoreState()
        c.setFont(BOLD, 11.5)
        c.setFillColor(HEAD)
        x = 10.0
        for ch in self.text:  # léger espacement des lettres
            c.drawString(x, 6, ch)
            x += stringWidth(ch, BOLD, 11.5) + 1.1
        c.saveState()
        path = c.beginPath()
        path.rect(x + 8, 9.6, self.width - x - 8, 1.4)
        c.clipPath(path, stroke=0, fill=0)
        _gradient(c, x + 8, 10, self.width, 10, (HexColor("#a5b4fc"), HexColor("#f3f2ff")))
        c.restoreState()


class Rail(Flowable):
    """Élément de la frise des expériences : trait vertical, pastille au début de chaque poste
    (pleine pour le poste en cours, comme sur le site)."""

    INDENT = 16

    def __init__(self, inner: Flowable, dot: bool = False, current: bool = False, pad: float = 2):
        super().__init__()
        self.inner, self.dot, self.current, self.pad = inner, dot, current, pad

    def wrap(self, avail_w, avail_h):
        w, h = self.inner.wrap(avail_w - self.INDENT, avail_h)
        self.width, self.height = avail_w, h + self.pad
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setStrokeColor(RAIL)
        c.setLineWidth(1.6)
        c.line(4.5, 0, 4.5, self.height)
        if self.dot:
            y = self.height - 7.5
            if self.current:
                c.setFillColor(TAG_BG)
                c.circle(4.5, y, 6.5, stroke=0, fill=1)
            c.setFillColor(PRIMARY if self.current else white)
            c.setStrokeColor(PRIMARY)
            c.setLineWidth(2.2)
            c.circle(4.5, y, 3.8, stroke=1, fill=1)
        self.inner.drawOn(c, self.INDENT, self.pad)


class Chips(Flowable):
    """Pastilles arrondies, renvoyées à la ligne selon la largeur disponible."""

    def __init__(self, items: list[str], size: float = 8.5, fill=SURFACE_2, border=LINE, color=TEXT):
        super().__init__()
        self.items = [t for t in (clean(i) for i in items) if t]
        self.size, self.fill, self.border, self.color = size, fill, border, color
        self.h, self.pad_x, self.gap = size + 6.5, size * 0.8, 4

    def wrap(self, avail_w, avail_h):
        self._rows, x, row = [], 0.0, 0
        for text in self.items:
            w = min(stringWidth(text, FONT, self.size) + 2 * self.pad_x, avail_w)
            if x and x + w > avail_w:
                x, row = 0.0, row + 1
            self._rows.append((text, x, row, w))
            x += w + self.gap
        n = row + 1 if self.items else 0
        self.width, self.height = avail_w, n * self.h + max(0, n - 1) * self.gap
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setLineWidth(0.8)
        for text, x, row, w in self._rows:
            y = self.height - (row + 1) * self.h - row * self.gap
            c.setStrokeColor(self.border)
            c.setFillColor(self.fill)
            c.roundRect(x, y, w, self.h, self.h / 2, stroke=1, fill=1)
            c.setFillColor(self.color)
            c.setFont(FONT, self.size)
            c.drawString(x + self.pad_x, y + self.h / 2 - self.size * 0.35, text)


class Card(Flowable):
    """Carte arrondie à fond lavande (comme les cartes du site) autour d'un contenu."""

    PAD = 9

    def __init__(self, inner: Flowable):
        super().__init__()
        self.inner = inner

    def wrap(self, avail_w, avail_h):
        _, h = self.inner.wrap(avail_w - 2 * self.PAD, avail_h)
        self.width, self.height = avail_w, h + 2 * self.PAD
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(SURFACE_2)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.9)
        c.roundRect(0, 0, self.width, self.height, 10, stroke=1, fill=1)
        self.inner.drawOn(c, self.PAD, self.PAD)


# --------------------------------------------------------------------------- #
# Bandeau d'en-tête (première page)
# --------------------------------------------------------------------------- #


class Header:
    NAME, HEADLINE_SIZE, CONTACT_SIZE, PILL_SIZE, AVATAR = 25, 12.5, 8.8, 8.8, 54

    def __init__(self, p: CVProfile):
        self.name = clean(p.full_name) or "Curriculum vitae"
        self.initials = initials(p.full_name)
        self.text_x = MARGIN + self.AVATAR + 16
        text_w = PAGE_W - MARGIN - self.text_x
        self.headline = simpleSplit(clean(p.headline), FONT, self.HEADLINE_SIZE, text_w)[:2]
        ct = p.contact
        self.contacts = [
            (clean(v), url)
            for v, url in (
                (ct.email, f"mailto:{ct.email.strip()}" if ct.email else None),
                (ct.phone, None),
                (ct.linkedin, _url(ct.linkedin)),
                (ct.github, _url(ct.github)),
                (ct.website, _url(ct.website)),
            )
            if v and clean(v)
        ]
        langs = ", ".join(
            clean(f"{lang.language} ({lang.level})" if lang.level else lang.language)
            for lang in p.languages[:4]
        )
        self.pills = [x for x in (clean(ct.location), langs) if x]
        self.height = (
            28 + 28 + 17 * len(self.headline) + (17 if self.contacts else 0) + (30 if self.pills else 6)
        )

    def draw(self, c):
        top, bottom = PAGE_H, PAGE_H - self.height
        c.saveState()
        path = c.beginPath()
        path.rect(0, bottom, PAGE_W, self.height)
        c.clipPath(path, stroke=0, fill=0)
        _gradient(c, 0, top, PAGE_W, bottom)
        # Halo clair en haut à droite, comme le bandeau d'accueil du site
        c.setFillColor(Color(1, 1, 1, alpha=0.07))
        c.circle(PAGE_W - 40, top - 10, 120, stroke=0, fill=1)
        c.circle(PAGE_W - 150, bottom - 30, 70, stroke=0, fill=1)
        c.restoreState()

        # Avatar : initiales dans un disque blanc translucide
        cy = top - 28 - self.AVATAR / 2
        cx = MARGIN + self.AVATAR / 2
        c.setFillColor(Color(1, 1, 1, alpha=0.18))
        c.setStrokeColor(Color(1, 1, 1, alpha=0.55))
        c.setLineWidth(1.5)
        c.circle(cx, cy, self.AVATAR / 2, stroke=1, fill=1)
        c.setFillColor(white)
        c.setFont(BOLD, 19)
        c.drawCentredString(cx, cy - 6.5, self.initials)

        x = self.text_x
        y = top - 28 - self.NAME * 0.78
        c.setFillColor(white)
        c.setFont(BOLD, self.NAME)
        c.drawString(x, y, self.name)
        c.setFillColor(SOFT)
        c.setFont(FONT, self.HEADLINE_SIZE)
        for line in self.headline:
            y -= 17
            c.drawString(x, y, line)
        if self.contacts:
            y -= 18
            c.setFont(FONT, self.CONTACT_SIZE)
            for i, (text, url) in enumerate(self.contacts):
                if i:
                    c.setFillColor(HexColor("#c7d2fe"))
                    c.drawString(x, y, "  ·  ")
                    x += stringWidth("  ·  ", FONT, self.CONTACT_SIZE)
                w = stringWidth(text, FONT, self.CONTACT_SIZE)
                if x + w > PAGE_W - MARGIN:
                    break
                c.setFillColor(white)
                c.drawString(x, y, text)
                if url:
                    c.linkURL(url, (x, y - 2, x + w, y + 9), relative=0)
                x += w
        if self.pills:
            y -= 28
            x = self.text_x
            for text in self.pills:
                w = min(stringWidth(text, FONT, self.PILL_SIZE) + 22, PAGE_W - MARGIN - x)
                if w < 40:
                    break
                c.setFillColor(Color(1, 1, 1, alpha=0.16))
                c.setStrokeColor(Color(1, 1, 1, alpha=0.4))
                c.setLineWidth(0.8)
                c.roundRect(x, y, w, 18, 9, stroke=1, fill=1)
                c.setFillColor(white)
                c.setFont(FONT, self.PILL_SIZE)
                c.drawString(x + 11, y + 5.8, text)
                x += w + 6


def _url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    return value if re.match(r"https?://", value) else f"https://{value}" if "." in value else None


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #

TABLE_STYLE = [
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ("TOPPADDING", (0, 0), (-1, -1), 0),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
]


def _tag(text: str, bg: str = "#e7e6fc", color: str = "#4f46e5") -> str:
    return (
        f'&nbsp;&nbsp;<font size="7.5" color="{color}" backColor="{bg}"><b>&nbsp;{esc(text)}&nbsp;</b></font>'
    )


def _experience(e: Experience) -> list[Flowable]:
    current = is_current(e.end)
    when = period(e.start, e.end) or clean(e.duration)
    top = Table(
        [[Paragraph(esc(e.title), STYLES["title"]), Paragraph(esc(when), STYLES["date"])]],
        colWidths=[None, 34 * mm],
        style=TableStyle(TABLE_STYLE),
    )
    items: list[Flowable] = [Rail(top, dot=True, current=current, pad=1)]
    org = []
    if e.company:
        org.append(f'<font color="#4f46e5"><b>{esc(e.company)}</b></font>')
    if e.location:
        org.append(esc(e.location))
    line = " · ".join(org)
    if e.contract_type and e.contract_type.strip().upper() != "CDI":
        line += _tag(e.contract_type)
    if current:
        line += _tag("En poste", "#dcfce7", "#16a34a")
    if line:
        items.append(Rail(Paragraph(line, STYLES["org"]), pad=4))
    points = [a for a in e.achievements if a.strip()]
    if e.description and e.description.strip():
        if points:
            items.append(Rail(Paragraph(emphasize(e.description), STYLES["desc"]), pad=2.5))
        else:
            points = [e.description]
    for a in points:
        items.append(Rail(Paragraph(emphasize(a), STYLES["check"], bulletText=CHECK), pad=2))
    techs = [t for t in e.technologies if t.strip()]
    if techs:
        items.append(Rail(Spacer(1, 2), pad=0))
        items.append(Rail(Chips(techs, size=7.5), pad=1))
    items.append(Rail(Spacer(1, 8), pad=0))
    return items


def _two_columns(label: str, content: Flowable, label_w: float = 40 * mm, gap: float = 7) -> Table:
    return Table(
        [[Paragraph(esc(label), STYLES["label"]), content]],
        colWidths=[label_w, BODY_W - label_w],
        style=TableStyle([*TABLE_STYLE, ("BOTTOMPADDING", (0, 0), (-1, -1), gap)]),
    )


def _story(p: CVProfile) -> list[Flowable]:
    story: list[Flowable] = []

    def section(title: str) -> None:
        story.append(CondPageBreak(30 * mm))
        story.append(SectionTitle(title))
        story.append(Spacer(1, 5))

    if p.summary.strip():
        section("Profil")
        story.append(Card(Paragraph(emphasize(p.summary), STYLES["body"])))
        story.append(Spacer(1, 12))

    if p.experiences:
        section("Expérience")
        for e in p.experiences:
            story.append(CondPageBreak(22 * mm))
            story += _experience(e)
        story.append(Spacer(1, 4))

    if p.projects:
        section("Projets")
        for pr in p.projects:
            text = f'<font color="#1e1b4b"><b>{esc(pr.name)}</b></font>'
            if pr.description:
                text += f" — {emphasize(pr.description)}"
            block: list[Flowable] = [Paragraph(text, STYLES["body"])]
            techs = [t for t in pr.technologies if t.strip()]
            if techs:
                block += [Spacer(1, 3), Chips(techs, size=7.5)]
            story.append(KeepTogether([*block, Spacer(1, 7)]))
        story.append(Spacer(1, 4))

    skills = p.merged_skills()
    if p.soft_skills:
        skills.append(("Savoir-être", p.soft_skills))
    if skills:
        section("Compétences")
        for category, items in skills:
            story.append(_two_columns(category, Chips(items)))
        story.append(Spacer(1, 6))

    if p.education or p.certifications:
        section("Formation & certifications")
        for ed in p.education:
            title = esc(ed.degree) + (f", {esc(ed.field)}" if ed.field else "")
            rest = ", ".join(x for x in (esc(ed.school), esc(ed.location)) if x)
            when = period(ed.start, ed.end)
            block = [
                Table(
                    [
                        [
                            Paragraph(f'<font color="#1e1b4b"><b>{title}</b></font>', STYLES["body"]),
                            Paragraph(esc(when), STYLES["date"]),
                        ]
                    ],
                    colWidths=[None, 34 * mm],
                    style=TableStyle(TABLE_STYLE),
                )  # fmt: skip
            ]
            if rest:
                block.append(Paragraph(f'<font color="#4f46e5"><b>{rest}</b></font>', STYLES["org"]))
            if ed.details:
                block.append(Paragraph(" · ".join(esc(d) for d in ed.details if d.strip()), STYLES["small"]))
            story.append(KeepTogether([*block, Spacer(1, 7)]))
        if p.certifications:
            story.append(Spacer(1, 2))
            story.append(_two_columns("Certifications", Chips(p.certifications, fill=TAG_BG, border=TAG_BG)))
        story.append(Spacer(1, 6))

    if p.languages or p.interests:
        section("Langues & centres d'intérêt")
        if p.languages:
            langs = [
                f"{lang.language} ({lang.level})" if lang.level else lang.language for lang in p.languages
            ]
            story.append(_two_columns("Langues", Chips(langs, fill=TAG_BG, border=TAG_BG, color=PRIMARY)))
        if p.interests:
            story.append(_two_columns("Centres d'intérêt", Chips(p.interests)))
    return story


def _footer(c, doc) -> None:
    """Fin liseré dégradé en bas de chaque page."""
    c.saveState()
    path = c.beginPath()
    path.rect(0, 0, PAGE_W, 4)
    c.clipPath(path, stroke=0, fill=0)
    _gradient(c, 0, 0, PAGE_W, 0)
    c.restoreState()


def render_cv_pdf(p: CVProfile) -> bytes:
    """PDF (A4) du CV à partir du profil, aux couleurs du site."""
    header = Header(p)
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"CV — {header.name}",
        author=header.name,
        creator="GetIAJob",
    )
    first = Frame(MARGIN, 14 * mm, BODY_W, PAGE_H - header.height - 8 * mm - 14 * mm, id="first", **NO_PAD)
    later = Frame(MARGIN, 14 * mm, BODY_W, PAGE_H - 28 * mm, id="later", **NO_PAD)

    def first_page(c, d):
        header.draw(c)
        _footer(c, d)

    doc.addPageTemplates(
        [PageTemplate("first", [first], onPage=first_page), PageTemplate("later", [later], onPage=_footer)]
    )
    doc.build([NextPageTemplate("later"), *_story(p)])
    return buffer.getvalue()
