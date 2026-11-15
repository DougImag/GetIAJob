"""Restitution des meilleures offres : tableau console + rapport Markdown."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from rich.console import Console
from rich.table import Table


def print_results(results: list[dict], console: Console | None = None) -> None:
    console = console or Console()
    if not results:
        console.print("[yellow]Aucune offre classée.[/yellow]")
        return
    table = Table(title="Meilleures offres de la semaine", show_lines=True)
    table.add_column("#", justify="right")
    table.add_column("Score", justify="right", style="bold green")
    table.add_column("Poste")
    table.add_column("Entreprise")
    table.add_column("Lieu")
    table.add_column("Verdict")
    for i, r in enumerate(results, 1):
        table.add_row(
            str(i),
            f"{r['final_score']:.0f}",
            f"[link={r['url']}]{r['title']}[/link]",
            r["company"] or "",
            r["location"] or "",
            r["summary"] or "",
        )
    console.print(table)


def render_markdown(results: list[dict], headline: str, day: date | None = None) -> str:
    day = day or date.today()
    lines = [f"# Top offres — {day:%d/%m/%Y}", "", f"Profil : **{headline}**", ""]
    for i, r in enumerate(results, 1):
        lines += [
            f"## {i}. [{r['title']}]({r['url']}) — {r['company'] or '?'}",
            "",
            f"- **Score** : {r['final_score']:.0f}/100 "
            f"(LLM : {r['llm_score'] if r['llm_score'] is not None else 'n/a'}, "
            f"similarité : {r['similarity']:.2f})",
            f"- **Lieu** : {r['location'] or '?'} · **Publiée** : {r['posted_at'] or '?'}"
            + (f" · **Salaire** : {r['salary']}" if r.get("salary") else ""),
        ]
        if r["summary"]:
            lines.append(f"- **Verdict** : {r['summary']}")
        if r["matching_skills"]:
            lines.append(f"- **Compétences en phase** : {', '.join(r['matching_skills'])}")
        if r["missing_skills"]:
            lines.append(f"- **Compétences manquantes** : {', '.join(r['missing_skills'])}")
        lines += [f"- ✅ {p}" for p in r["pros"]]
        lines += [f"- ⚠️ {c}" for c in r["cons"]]
        lines.append("")
    return "\n".join(lines)


def write_report(results: list[dict], headline: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"top_offres_{date.today().isoformat()}.md"
    path.write_text(render_markdown(results, headline), encoding="utf-8")
    return path
