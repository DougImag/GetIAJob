"""Interface en ligne de commande : `getiajob --help`."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.logging import RichHandler
from sqlalchemy import select, text

from getiajob.config import get_settings, load_preferences
from getiajob.report import print_results, write_report

app = typer.Typer(no_args_is_help=True, help="Trouve les offres d'emploi qui collent à ton CV.")
console = Console()


@app.callback()
def _setup(verbose: bool = typer.Option(False, "--verbose", "-v", help="Logs détaillés")) -> None:
    level = "DEBUG" if verbose else get_settings().log_level
    logging.basicConfig(
        level=level, format="%(message)s", handlers=[RichHandler(console=console, show_path=False)]
    )
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@app.command("init-db")
def init_db_cmd() -> None:
    """Crée l'extension pgvector et les tables."""
    from getiajob.db import init_db

    init_db()
    console.print("[green]Base de données initialisée.[/green]")


@app.command()
def doctor() -> None:
    """Vérifie l'accès à PostgreSQL et à Ollama (et la présence des modèles)."""
    from getiajob.db import get_engine

    s = get_settings()
    ok = True
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        console.print("[green]✔ PostgreSQL joignable[/green]")
    except Exception as exc:
        ok = False
        console.print(f"[red]✘ PostgreSQL : {exc}[/red]")
    try:
        tags = httpx.get(f"{s.ollama_base_url}/api/tags", timeout=10).json()
        names = {m["name"] for m in tags.get("models", [])}
        console.print(f"[green]✔ Ollama joignable ({s.ollama_base_url})[/green]")
        for model in (s.llm_model, s.embedding_model):
            if model in names or f"{model}:latest" in names:
                console.print(f"[green]  ✔ modèle {model}[/green]")
            else:
                ok = False
                console.print(f"[yellow]  ✘ modèle {model} absent → getiajob pull-models[/yellow]")
    except Exception as exc:
        ok = False
        console.print(f"[red]✘ Ollama ({s.ollama_base_url}) : {exc}[/red]")
    raise typer.Exit(0 if ok else 1)


@app.command("pull-models")
def pull_models() -> None:
    """Télécharge dans Ollama le LLM et le modèle d'embedding configurés."""
    s = get_settings()
    for model in (s.llm_model, s.embedding_model):
        console.print(f"Téléchargement de {model}… (peut prendre plusieurs minutes)")
        r = httpx.post(f"{s.ollama_base_url}/api/pull", json={"model": model, "stream": False}, timeout=None)
        r.raise_for_status()
        console.print(f"[green]✔ {model}[/green]")


@app.command("analyze-cv")
def analyze_cv_cmd(
    cv: Path = typer.Argument(..., help="Chemin du CV (.pdf, .docx, .md, .txt)"),
    force: bool = typer.Option(False, help="Ré-analyse même si le CV est déjà en base"),
) -> None:
    """Agent 1 seul : analyse le CV et affiche le profil extrait."""
    from getiajob.agents.cv_analyzer import analyze_cv

    cv_id, profile = analyze_cv(cv, force=force)
    console.print(f"[bold]CV #{cv_id}[/bold]")
    console.print_json(profile.model_dump_json())


@app.command()
def run(
    cv: Path = typer.Argument(..., help="Chemin du CV (.pdf, .docx, .md, .txt)"),
    skip_scrape: bool = typer.Option(False, help="N'interroge pas LinkedIn, trie les offres en base"),
    force_cv: bool = typer.Option(False, help="Ré-analyse le CV"),
    force_rank: bool = typer.Option(False, help="Ré-évalue toutes les offres avec le LLM"),
) -> None:
    """Pipeline complet : analyse du CV → collecte LinkedIn → tri des meilleures offres."""
    from getiajob.db import init_db
    from getiajob.graph import build_graph

    init_db()
    state = build_graph().invoke(
        {
            "cv_path": str(cv),
            "skip_scrape": skip_scrape,
            "force_cv": force_cv,
            "force_rank": force_rank,
        }
    )
    results = state.get("results", [])
    print_results(results, console)
    if results:
        report = write_report(results, state["profile"]["headline"], get_settings().output_dir)
        console.print(f"Rapport : [bold]{report}[/bold]")


@app.command()
def top(limit: int = typer.Option(10, help="Nombre d'offres à afficher")) -> None:
    """Affiche le dernier classement en base pour le CV le plus récent."""
    from getiajob.agents.ranker import top_matches
    from getiajob.db import CVRecord, session_scope

    days = load_preferences().search.days
    with session_scope() as session:
        cv_id = session.scalar(select(CVRecord.id).order_by(CVRecord.created_at.desc()).limit(1))
        if cv_id is None:
            console.print("[yellow]Aucun CV en base.[/yellow]")
            raise typer.Exit(1)
        since = datetime.now(UTC) - timedelta(days=days)
        print_results(top_matches(session, cv_id, limit, since), console)


@app.command("mail-sync")
def mail_sync(
    dry_run: bool = typer.Option(False, "--dry-run", help="Analyse sans rien enregistrer ni supprimer"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="N'affiche rien s'il n'y a aucun nouveau mail"),
) -> None:
    """Relève les alertes e-mail (APEC) et importe leurs offres. Conçu pour une tâche cron.

    Code de sortie : 0 = OK (ou relève déjà en cours), 1 = erreur (boîte injoignable, identifiants…).
    """
    from getiajob.services.mail_alerts import STATUS_LABELS, sync_mailbox

    out = Console(width=200, soft_wrap=True)  # lignes entières dans le journal cron

    report = sync_mailbox(dry_run=dry_run)
    stamp = f"{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z}"
    if report.error:
        out.print(f"{stamp} [red]✘ Relève impossible : {report.error}[/red]")
        raise typer.Exit(1)
    if report.skipped:
        if not quiet:
            out.print(f"{stamp} Relève déjà en cours ailleurs, rien à faire.")
        return
    if quiet and not report.results and not report.deleted:
        return
    mode = " (aperçu, rien n'est enregistré)" if dry_run else ""
    out.print(
        f"{stamp} {len(report.results)} nouveau(x) mail(s), {report.imported} alerte(s) importée(s), "
        f"{report.new_offers} nouvelle(s) offre(s), {report.deleted} mail(s) supprimé(s) "
        f"en {report.seconds:.1f} s{mode}"
    )
    for r in report.results:
        who = r.user_label or r.sender or "?"
        out.print(
            f"  • {STATUS_LABELS.get(r.status, r.status)} — {r.subject} — {who} — {len(r.postings)} offre(s)"
        )


@app.command()
def monitor(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Affiche les constats sans rien enregistrer ni envoyer"
    ),
    no_notify: bool = typer.Option(False, "--no-notify", help="Met à jour les alertes sans envoyer d'e-mail"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="N'affiche rien s'il n'y a rien de nouveau"),
) -> None:
    """Supervision : détecte les problèmes, tient à jour les alertes et prévient les administrateurs.

    Conçu pour une tâche cron (scripts/cron-monitor.sh). Code de sortie : 0 = OK, 1 = échec d'envoi.
    """
    from getiajob.db import init_db
    from getiajob.services.alerting import SEVERITY_LABELS, run_monitor

    out = Console(width=200, soft_wrap=True)
    init_db()
    report = run_monitor(dry_run=dry_run, notify=not no_notify)
    stamp = f"{datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z}"
    p = report.plan
    if quiet and not p.to_notify and not report.send_error:
        return
    mode = " (aperçu, rien n'est enregistré ni envoyé)" if dry_run else ""
    out.print(
        f"{stamp} {len(report.findings)} problème(s) : {len(p.opened)} nouveau(x), "
        f"{len(p.reminded)} rappel(s), "
        f"{len(p.resolved)} résolu(s){mode}"
    )
    for f in report.findings:
        out.print(f"  • [{SEVERITY_LABELS.get(f.severity, f.severity)}] {f.message}")
    for key in p.resolved:
        out.print(f"  ✓ résolu : {report.resolved_messages.get(key, key)}")
    if report.sent:
        out.print(f"  ✉ notification envoyée à {len(report.recipients)} destinataire(s)")
    if report.send_error:
        out.print(f"  [red]✘ notification non envoyée : {report.send_error}[/red]")
        raise typer.Exit(1)


@app.command("record-backup")
def record_backup(
    file: str = typer.Argument(..., help="Nom du fichier de sauvegarde"),
    size: int = typer.Option(0, help="Taille en octets"),
    failed: bool = typer.Option(False, "--failed", help="La sauvegarde a échoué"),
    detail: str = typer.Option("", help="Message d'erreur"),
) -> None:
    """Enregistre une sauvegarde de la base (appelé par scripts/backup-db.sh) pour la supervision."""
    from getiajob.services.monitoring import audit, record

    record("backup", "pg_dump", ok=not failed, count=size // 1024, detail=detail or file)
    if not failed:
        audit(None, "backup", file, f"{size / 1_048_576:.1f} Mo")


@app.command()
def web(
    port: int = typer.Option(8501, help="Port HTTP"),
    host: str = typer.Option("0.0.0.0", help="Adresse d'écoute"),
) -> None:
    """Lance l'interface web Streamlit."""
    import os
    import sys

    script = Path(__file__).parent / "web" / "app.py"
    args = [sys.executable, "-m", "streamlit", "run", str(script)]
    args += ["--server.port", str(port), "--server.address", host]
    os.execv(sys.executable, args)


if __name__ == "__main__":
    app()
