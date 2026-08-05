from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from .config import load_config
from .extractor import Extractor
from .analyzer import synthesize
from .reporter import write_reports

app = typer.Typer()
console = Console()


@app.command()
def research(
    seed: str = typer.Argument(..., help="Search seed, e.g. 'Etsy eco-friendly wedding invitations minimalist'"),
    config: Optional[Path] = typer.Option(None, help="Path to config.yaml"),
    limit: Optional[int] = typer.Option(None, help="Search result limit (overrides config)"),
    output: Optional[Path] = typer.Option(None, help="Output directory (overrides config)"),
):
    cfg = load_config(str(config) if config else None)
    if limit is not None:
        cfg.search_limit = limit
    if output is not None:
        cfg.output_dir = str(output)
    cfg.seed = seed
    console.print(
        Panel(f"[bold gold]KAIOS[/bold gold] — PIA-001 v1\nSeed: {seed}", expand=False)
    )
    extractor = Extractor(marketplace=cfg.marketplace, limit=cfg.search_limit)
    snippets = extractor.gather(seed)
    if not snippets:
        console.print("[red]No evidence gathered. Check search connectivity.[/red]")
        raise typer.Exit(1)
    opps = synthesize(snippets, seed, cfg.model)
    if not opps:
        console.print("[red]No opportunities synthesized.[/red]")
        raise typer.Exit(1)
    md_path, json_path = write_reports(seed, opps, cfg)
    console.print(f"[green]Reports written to[/green]\n  {md_path}\n  {json_path}")


def main():
    app()


if __name__ == "__main__":
    main()
