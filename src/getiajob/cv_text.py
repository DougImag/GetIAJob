"""Extraction du texte d'un CV en respectant sa mise en page.

Les CV sont souvent en plusieurs colonnes (barre latérale + colonne principale). Une extraction
naïve mélange les colonnes et sépare les titres de leur contenu, ce qui perd le LLM. Ici :
- PDF : blocs de texte PyMuPDF, détection de la gouttière entre colonnes, lecture colonne par
  colonne (de haut en bas), les blocs pleine largeur servant de séparateurs ;
- DOCX : paragraphes, tableaux et zones de texte dans l'ordre du document ;
- titres de sections reconnus et balisés « ## TITRE » pour guider le LLM.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# Titres de sections usuels (minuscules, sans accents) → catégorie
SECTION_TITLES: dict[str, set[str]] = {
    "summary": {
        "profil",
        "a propos",
        "a propos de moi",
        "about",
        "about me",
        "resume",
        "summary",
        "objectif",
    },
    "contact": {"contact", "coordonnees", "informations personnelles"},
    "experience": {
        "experience",
        "experiences",
        "experience professionnelle",
        "experiences professionnelles",
        "parcours professionnel",
        "work experience",
        "professional experience",
        "employment",
        "stages",
        "parcours",
        "missions",
        "experiences significatives",
        "experiences cles",
    },
    "education": {
        "formation",
        "formations",
        "etudes",
        "education",
        "diplomes",
        "cursus",
        "parcours academique",
        "parcours scolaire",
        "scolarite",
    },
    "skills": {
        "competences",
        "competences techniques",
        "competences cles",
        "skills",
        "hard skills",
        "soft skills",
        "savoir-faire",
        "savoir-etre",
        "qualites",
        "programmation",
        "outils",
        "technologies",
        "informatique",
        "stack technique",
    },
    "languages": {"langues", "languages", "langues etrangeres", "langues vivantes"},
    "projects": {"projets", "projects", "projets personnels", "projets academiques", "realisations"},
    "certifications": {"certifications", "certificats", "certification"},
    "interests": {
        "centres d'interet",
        "centres d interet",
        "centre d'interet",
        "loisirs",
        "hobbies",
        "interets",
        "passions",
        "activites",
        "activites extra-professionnelles",
    },
    "other": {
        "benevolat",
        "engagements",
        "engagement associatif",
        "vie associative",
        "publications",
        "references",
        "distinctions",
        "prix",
    },
    "courses": {"cours", "cours suivis", "matieres", "modules"},
}
_ALL_TITLES = {t: cat for cat, titles in SECTION_TITLES.items() for t in titles}

_SPACED_WORD = re.compile(r"(?<!\S)((?:\w ){2,}\w)(?!\S)")


def _strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn")


def collapse_spaced_letters(line: str) -> str:
    """« E X P E R I E N C E S » → « EXPERIENCES » (titres à lettres espacées)."""
    return _SPACED_WORD.sub(lambda m: m.group(1).replace(" ", ""), line)


_COMPOUND = re.compile(r"\s*(?:&|/|\+|,|\bet\b|\band\b)\s*")


def _title_key(value: str) -> str:
    key = re.sub(r"[’`]", "'", value)
    key = re.sub(r"[^\w' -]", " ", key)
    return re.sub(r"\s+", " ", key).strip(" -:")


def section_of(line: str) -> str | None:
    """Catégorie de section si la ligne est un titre de section connu.

    Titre composé (« FORMATION & CERTIFICATIONS », « LANGUES ET LOISIRS ») : catégorie de sa
    première partie reconnue, à condition que toutes ses parties soient des titres connus.
    """
    text = _strip_accents(collapse_spaced_letters(line)).lower()
    if category := _ALL_TITLES.get(_title_key(text)):
        return category
    parts = [_title_key(part) for part in _COMPOUND.split(text)]
    parts = [part for part in parts if part]
    if len(parts) > 1 and all(part in _ALL_TITLES for part in parts):
        return _ALL_TITLES[parts[0]]
    return None


def _looks_like_title(line: str) -> bool:
    """Ligne courte en majuscules sans chiffres : probablement un titre non répertorié."""
    letters = [c for c in line if c.isalpha()]
    return (
        4 <= len(letters) <= 30
        and len(line.split()) <= 4
        and not any(c.isdigit() for c in line)
        and all(c.isupper() for c in letters)
        and "@" not in line
    )


def mark_headings(text: str) -> str:
    """Normalise les lignes et balise les titres de sections par « ## ».

    Dans la section des expériences, une ligne en majuscules qui n'est pas un titre connu est le
    nom d'un employeur (« GROUPE BPCE ») : elle reste dans le texte, sans balise, pour ne pas être
    séparée du poste qu'elle accompagne.
    """
    out: list[str] = []
    current: str | None = None  # catégorie de la section en cours
    for raw in text.split("\n"):
        line = collapse_spaced_letters(raw.replace(" ", " ").strip())
        line = re.sub(r"[ \t]+", " ", line)
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        category = section_of(line)
        if category or (_looks_like_title(line) and current != "experience"):
            previous = next((x for x in reversed(out) if x), "")
            merged = f"{previous[3:]} {line.upper()}"
            if previous.startswith("## ") and section_of(merged):
                # Titre sur deux lignes (« CENTRES » / « D'INTÉRÊT »)
                while out and not out[-1]:
                    out.pop()
                out[-1] = f"## {merged}"
                current = section_of(merged)
                continue
            if out and out[-1] != "":
                out.append("")
            out.append(f"## {line.upper()}")
            current = category
        else:
            out.append(line)
    return "\n".join(out).strip()


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #


@dataclass
class _Block:
    """Fragment de ligne positionné (un bloc PyMuPDF est découpé en lignes, voire en morceaux)."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block_id: int = 0

    @property
    def area(self) -> float:
        return max(1.0, (self.x1 - self.x0) * (self.y1 - self.y0))


def _find_gutter(blocks: list[_Block], width: float) -> float | None:
    """Abscisse séparant deux colonnes (traversée par peu de texte), ou None si une seule colonne."""
    total = sum(b.area for b in blocks)
    best: tuple[float, float] | None = None
    x = width * 0.15
    while x <= width * 0.85:
        left = sum(b.area for b in blocks if b.x1 <= x)
        right = sum(b.area for b in blocks if b.x0 >= x)
        if left >= 0.1 * total and right >= 0.1 * total:
            crossing = sum(b.area for b in blocks if b.x0 < x < b.x1)
            if best is None or crossing < best[1]:
                best = (x, crossing)
        x += 2
    if best and best[1] <= 0.2 * total:
        return best[0]
    return None


def _order_blocks(blocks: list[_Block], width: float) -> list[_Block]:
    """Ordre de lecture : bandes séparées par les blocs pleine largeur, colonne gauche puis droite."""
    gutter = _find_gutter(blocks, width)
    if gutter is None:
        return sorted(blocks, key=lambda b: (round(b.y0), b.x0))

    wide = sorted((b for b in blocks if b.x0 < gutter < b.x1), key=lambda b: b.y0)
    ordered: list[_Block] = []
    top = float("-inf")
    for separator in [*wide, None]:
        bottom = separator.y0 if separator else float("inf")
        band = [b for b in blocks if b not in wide and top <= b.y0 < bottom]
        for column in (
            [b for b in band if b.x1 <= gutter],
            [b for b in band if b.x0 >= gutter],
        ):
            ordered += sorted(column, key=lambda b: (round(b.y0), b.x0))
        if separator:
            ordered.append(separator)
            top = separator.y0
    return ordered


def _page_fragments(page) -> list[_Block]:
    """Lignes de la page, scindées là où un grand espace horizontal sépare deux colonnes."""
    fragments: list[_Block] = []
    for block_id, block in enumerate(page.get_text("dict")["blocks"]):
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            current: list[dict] = []
            for span in spans:
                if current:
                    gap = span["bbox"][0] - current[-1]["bbox"][2]
                    if gap > 3 * max(span["size"], 6):
                        fragments.append(_fragment(current, block_id))
                        current = []
                current.append(span)
            if current:
                fragments.append(_fragment(current, block_id))
    return fragments


def _fragment(spans: list[dict], block_id: int) -> _Block:
    return _Block(
        x0=min(s["bbox"][0] for s in spans),
        y0=min(s["bbox"][1] for s in spans),
        x1=max(s["bbox"][2] for s in spans),
        y1=max(s["bbox"][3] for s in spans),
        text=" ".join(s["text"].strip() for s in spans),
        block_id=block_id,
    )


def _join(fragments: list[_Block]) -> str:
    """Une ligne par fragment, une ligne vide quand on change de bloc."""
    lines: list[str] = []
    previous: int | None = None
    for f in fragments:
        if previous is not None and f.block_id != previous:
            lines.append("")
        lines.append(f.text)
        previous = f.block_id
    return "\n".join(lines)


def pdf_to_text(data: bytes) -> str:
    import pymupdf

    pages: list[str] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            fragments = _page_fragments(page)
            pages.append(_join(_order_blocks(fragments, page.rect.width)))
    return "\n\n".join(pages)


# --------------------------------------------------------------------------- #
# DOCX
# --------------------------------------------------------------------------- #


def docx_to_text(data: bytes) -> str:
    """Paragraphes du corps, des tableaux et des zones de texte, dans l'ordre du document."""
    from docx import Document
    from docx.oxml.ns import qn

    body = Document(io.BytesIO(data)).element.body
    lines: list[str] = []
    for paragraph in body.iter(qn("w:p")):
        # Les zones de texte existent en double (mc:Choice + mc:Fallback) : on ignore la copie.
        if any(ancestor.tag.endswith("}Fallback") for ancestor in paragraph.iterancestors()):
            continue
        text = "".join(node.text or "" for node in paragraph.iter(qn("w:t"))).strip()
        if text and (not lines or lines[-1] != text):
            lines.append(text)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #


def extract_cv_text(data: bytes, file_name: str) -> str:
    """Texte du CV, dans l'ordre de lecture, avec les titres de sections balisés."""
    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        text = pdf_to_text(data)
    elif suffix == ".docx":
        text = docx_to_text(data)
    else:
        text = data.decode("utf-8", errors="replace")
    return mark_headings(text)


def detected_sections(text: str) -> list[str]:
    """Titres « ## » présents dans un texte balisé (pour information / débogage)."""
    return [line[3:] for line in text.split("\n") if line.startswith("## ")]


def section_text(text: str, category: str) -> str:
    """Contenu des sections d'une catégorie (« experience »…) d'un texte balisé ; "" si absente.

    Une section s'arrête au titre « ## » suivant. Plusieurs sections de la même catégorie
    (« EXPÉRIENCES » puis « STAGES ») sont mises bout à bout.
    """
    parts: list[str] = []
    inside = False
    for line in text.split("\n"):
        if line.startswith("## "):
            inside = section_of(line[3:]) == category
            if inside:
                parts.append(line)
            continue
        if inside:
            parts.append(line)
    return "\n".join(parts).strip()
