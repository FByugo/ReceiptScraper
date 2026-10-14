"""Command line entry point: `receipt-scraper run urls.txt` and `receipt-scraper verify out/`."""
from __future__ import annotations

import logging
from pathlib import Path

import typer

from .core import read_url_list, run as run_pages, verify as verify_dir

app = typer.Typer(add_completion=False, help="Pull public pages and keep a checkable receipt for each one.")


@app.callback()
def _setup(verbose: bool = typer.Option(False, "--verbose", "-v", help="Show every page as it is fetched.")):
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")


@app.command()
def run(
    urls_file: Path = typer.Argument(..., exists=True, readable=True, help="Text file, one URL per line."),
    out: Path = typer.Option(Path("out"), "--out", "-o", help="Folder for results.csv, manifest.json and receipts/."),
    delay: float = typer.Option(1.0, help="Seconds to wait between requests to the same site."),
    timeout: float = typer.Option(20.0, help="Seconds before a request gives up."),
    respect_robots: bool = typer.Option(True, "--respect-robots/--ignore-robots", help="Skip pages robots.txt disallows."),
):
    """Fetch every URL in URLS_FILE and save the results with receipts."""
    urls = read_url_list(urls_file)
    if not urls:
        typer.echo("no URLs in the file", err=True)
        raise typer.Exit(2)
    summary = run_pages(urls, out, delay=delay, timeout=timeout, respect_robots=respect_robots)
    parts = ", ".join(f"{k} {v}" for k, v in sorted(summary.counts.items()))
    typer.echo(f"{summary.total} pages: {parts}. Results in {out}/results.csv")
    if summary.counts.get("ok", 0) == 0:
        raise typer.Exit(1)


@app.command()
def verify(out: Path = typer.Argument(Path("out"), exists=True, file_okay=False, help="A folder made by `run`.")):
    """Re-hash every saved page and check it still matches the manifest."""
    problems = verify_dir(out)
    for p in problems:
        typer.echo(p, err=True)
    if problems:
        raise typer.Exit(1)
    typer.echo("every saved page matches its recorded hash")


if __name__ == "__main__":
    app()
