"""Fetch a list of public pages and keep a checkable receipt for every one.

Each page gets: the HTTP status, the final URL after redirects, the raw bytes saved to disk,
a SHA-256 of those bytes, the time it was fetched, and the extracted title, description,
first heading, word count and visible text. Pages that block readers are reported as blocked,
never guessed at.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("receipt_scraper")

USER_AGENT = "ReceiptScraper/1.0 (+public page research; respects robots.txt)"
THIN_CHARS = 200
BLOCK_MARKERS = (
    "just a moment...", "attention required", "verify you are human", "are you a robot",
    "captcha", "access denied", "cf-chl-", "enable javascript and cookies to continue",
)


@dataclass
class PageRecord:
    url: str
    outcome: str  # ok · thin · blocked · robots · not_html · error
    status: int | None = None
    final_url: str = ""
    fetched_at: str = ""
    sha256: str = ""
    bytes: int = 0
    content_type: str = ""
    title: str = ""
    description: str = ""
    h1: str = ""
    words: int = 0
    note: str = ""
    receipt: str = ""
    text_file: str = ""


@dataclass
class RunSummary:
    total: int = 0
    counts: dict = field(default_factory=dict)
    out_dir: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_url_list(path: Path) -> list[str]:
    """One URL per line. Blank lines and lines starting with # are skipped; duplicates are dropped in order."""
    seen, urls = set(), []
    for line in path.read_text(encoding="utf-8").splitlines():
        u = line.strip()
        if not u or u.startswith("#"):
            continue
        if not urlsplit(u).scheme:
            u = "https://" + u
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


class RobotsCache:
    """Caches one robots.txt per host. An unreachable robots.txt is treated as allow, and that is logged."""

    def __init__(self, session: requests.Session, timeout: float):
        self.session, self.timeout, self._cache = session, timeout, {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._cache:
            rp = robotparser.RobotFileParser()
            try:
                r = self.session.get(base + "/robots.txt", timeout=self.timeout)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException as e:
                log.warning("robots.txt unreachable for %s (%s), treating as allow", base, e)
                rp.parse([])
            self._cache[base] = rp
        return self._cache[base].can_fetch(USER_AGENT, url)


def extract(html: bytes) -> dict:
    """Pull title, meta description, first h1 and visible text out of an HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    return {
        "title": (soup.title.get_text(strip=True) if soup.title else "")[:300],
        "description": (meta.get("content", "").strip() if meta else "")[:500],
        "h1": (soup.h1.get_text(" ", strip=True) if soup.h1 else "")[:300],
        "text": text,
    }


def looks_blocked(status: int, text_lower: str) -> bool:
    if status in (401, 403, 429, 503):
        return True
    return len(text_lower) < 2000 and any(m in text_lower for m in BLOCK_MARKERS)


def fetch_one(session: requests.Session, robots: RobotsCache | None, url: str,
              receipts: Path, timeout: float) -> PageRecord:
    rec = PageRecord(url=url, outcome="error", fetched_at=_now())
    if robots is not None and not robots.allowed(url):
        rec.outcome, rec.note = "robots", "disallowed by robots.txt, not fetched"
        return rec
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        rec.note = f"{type(e).__name__}: {e}"[:300]
        return rec

    body = r.content
    rec.status, rec.final_url, rec.bytes = r.status_code, r.url, len(body)
    rec.content_type = r.headers.get("Content-Type", "").split(";")[0].strip()
    rec.sha256 = hashlib.sha256(body).hexdigest()
    raw_path = receipts / f"{rec.sha256[:16]}.raw"
    raw_path.write_bytes(body)
    rec.receipt = raw_path.name

    if "html" not in rec.content_type and not body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        # status first: a refusal sent as plain text (Wikipedia does this) is a block, not a document
        snippet = body[:2000].decode("utf-8", "replace").lower()
        if looks_blocked(r.status_code, snippet):
            rec.outcome, rec.note = "blocked", f"HTTP {r.status_code}; refusal sent as {rec.content_type or 'unknown type'}"
        elif r.status_code >= 400:
            rec.outcome, rec.note = "error", f"HTTP {r.status_code}"
        else:
            rec.outcome, rec.note = "not_html", f"saved as-is ({rec.content_type or 'no content type'})"
        return rec

    if not body.strip():
        # nothing to parse; handing b"" to the parser only produced a decode warning (2 of them on the example run)
        rec.outcome = "blocked" if looks_blocked(r.status_code, "") else ("error" if r.status_code >= 400 else "thin")
        rec.note = f"HTTP {r.status_code}, empty body"
        return rec

    page = extract(body)
    rec.title, rec.description, rec.h1 = page["title"], page["description"], page["h1"]
    rec.words = len(page["text"].split())
    text_path = receipts / f"{rec.sha256[:16]}.txt"
    text_path.write_text(page["text"], encoding="utf-8")
    rec.text_file = text_path.name

    if looks_blocked(r.status_code, page["text"].lower()):
        rec.outcome, rec.note = "blocked", f"HTTP {r.status_code}; the page refused a reader, contents not guessed"
    elif r.status_code >= 400:
        rec.outcome, rec.note = "error", f"HTTP {r.status_code}"
    elif len(page["text"]) < THIN_CHARS:
        rec.outcome, rec.note = "thin", f"only {len(page['text'])} visible characters: either a short page or one built by JavaScript, check the receipt"
    else:
        rec.outcome = "ok"
    return rec


def run(urls: list[str], out_dir: Path, delay: float = 1.0, timeout: float = 20.0,
        respect_robots: bool = True, session: requests.Session | None = None) -> RunSummary:
    """Fetch every URL, write results.csv and manifest.json into out_dir, return the counts."""
    receipts = out_dir / "receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    robots = RobotsCache(session, timeout) if respect_robots else None

    records: list[PageRecord] = []
    last_host_hit: dict[str, float] = {}
    for i, url in enumerate(urls, 1):
        host = urlsplit(url).netloc
        wait = delay - (time.monotonic() - last_host_hit.get(host, -1e9))
        if wait > 0:
            time.sleep(wait)
        rec = fetch_one(session, robots, url, receipts, timeout)
        last_host_hit[host] = time.monotonic()
        log.info("[%d/%d] %-8s %s %s", i, len(urls), rec.outcome, rec.status or "-", url)
        records.append(rec)

    fields = list(PageRecord.__dataclass_fields__)
    with open(out_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for rec in records:
            w.writerow(asdict(rec))
    manifest = {"created": _now(), "user_agent": USER_AGENT, "respect_robots": respect_robots,
                "pages": [asdict(r) for r in records]}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    counts: dict[str, int] = {}
    for rec in records:
        counts[rec.outcome] = counts.get(rec.outcome, 0) + 1
    return RunSummary(total=len(records), counts=counts, out_dir=str(out_dir))


def verify(out_dir: Path) -> list[str]:
    """Re-hash every saved page and compare it with the manifest. Returns a list of problems (empty = all match)."""
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    for p in manifest["pages"]:
        if not p.get("receipt"):
            continue
        path = out_dir / "receipts" / p["receipt"]
        if not path.exists():
            problems.append(f"missing receipt {p['receipt']} for {p['url']}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != p["sha256"]:
            problems.append(f"hash mismatch for {p['url']}: manifest {p['sha256'][:16]} file {actual[:16]}")
    return problems
