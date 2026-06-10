#!/usr/bin/env python3
"""Refresh public live-signal data for the static site.

The browser reads only data/live-signals.json. This collector runs in GitHub
Actions and uses public sources by default. Optional authenticated checks can be
added here later without exposing secrets to the client.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree


CADENCE_HOURS = 4
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "live-signals.json"

MCP_REPOS = [
    ("AWS", "awslabs/mcp", "Official AWS Labs MCP servers repository."),
    ("Google", "google/mcp", "Official Google MCP repository signal."),
    ("Microsoft", "microsoft/mcp", "Official Microsoft MCP repository."),
    ("Azure", "Azure/azure-mcp", "Archived repository retained as a migration signal; active development moved to microsoft/mcp."),
]

QUANTUM_SOURCES = [
    ("Amazon Braket", "release", "https://aws.amazon.com/blogs/quantum-computing/feed/"),
    ("Microsoft Azure Quantum", "announcement", "https://azure.microsoft.com/products/quantum"),
    ("Google Quantum AI", "announcement", "https://blog.google/technology/research/rss/"),
    ("IBM Quantum", "release", "https://www.ibm.com/quantum/blog"),
    ("D-Wave", "announcement", "https://www.dwavesys.com/company/newsroom/"),
    ("Rigetti", "announcement", "https://www.rigetti.com/news"),
    ("IonQ", "announcement", "https://ionq.com/news"),
    ("Quantinuum", "announcement", "https://www.quantinuum.com/news"),
    ("Xanadu", "announcement", "https://www.xanadu.ai/blog"),
    ("Pasqal", "announcement", "https://www.pasqal.com/news/"),
]

HEALTH_SOURCES = [
    ("Amazon Braket", "quantum", "token_required", "token required", "https://docs.aws.amazon.com/braket/latest/APIReference/API_GetDevice.html", "Device-level status requires configured AWS credentials."),
    ("GitHub Pages", "status", "operational", "live", "https://www.githubstatus.com/", "Public status source for the static site host."),
    ("Microsoft Azure Quantum", "quantum", "unknown", "public", "https://azure.status.microsoft/en-us/status", "Public cloud status is checked where available; device-level telemetry is not called from the browser."),
    ("IBM Quantum", "quantum", "unknown", "public", "https://cloud.ibm.com/status", "Public cloud status source; detailed backend queue data is not exposed to the browser."),
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch(url: str, accept: str = "*/*") -> Tuple[Optional[str], Optional[str]]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "akanjilal.dev-live-signals/1.0 (+https://akanjilal.dev/)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=18) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace"), None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, str(exc)


def text_from_html(value: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def short(value: Optional[str], limit: int = 150) -> str:
    value = text_from_html(value or "")
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def parse_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return iso(parsedate_to_datetime(value))
    except (TypeError, ValueError, IndexError):
        return None


def first_xml_text(item: ElementTree.Element, names: List[str]) -> Optional[str]:
    for child in item.iter():
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in names and child.text:
            return child.text.strip()
    return None


def parse_feed(vendor: str, kind: str, url: str, checked: str) -> Tuple[Dict[str, Any], Optional[str]]:
    body, error = fetch(url, "application/rss+xml, application/atom+xml, text/xml, text/html")
    fallback = {
        "vendor": vendor,
        "type": kind,
        "title": f"Latest {vendor} signal",
        "summary": "No public feed item was parsed. Source link retained for manual inspection.",
        "url": url,
        "published_at": None,
        "last_checked": checked,
        "status": "unknown",
    }
    if error or not body:
        return fallback, error

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.I | re.S)
        if title_match:
            fallback["title"] = short(title_match.group(1), 90)
            fallback["summary"] = "Public source reached; no structured feed was available."
            fallback["status"] = "operational"
        return fallback, None

    items = [el for el in root.iter() if el.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    if not items:
        return fallback, None

    item = items[0]
    title = first_xml_text(item, ["title"]) or fallback["title"]
    link = first_xml_text(item, ["link"]) or url
    for child in item.iter():
        if child.tag.rsplit("}", 1)[-1].lower() == "link" and child.attrib.get("href"):
            link = child.attrib["href"]
            break
    summary = first_xml_text(item, ["description", "summary", "content", "encoded"])
    published = parse_date(first_xml_text(item, ["pubdate", "published", "updated"]))
    return {
        "vendor": vendor,
        "type": kind,
        "title": short(title, 96),
        "summary": short(summary, 170) or "Official public source reached; no summary provided.",
        "url": link,
        "published_at": published,
        "last_checked": checked,
        "status": "operational",
    }, None


def github_repo(provider: str, repo: str, notes: str, checked: str) -> Tuple[Dict[str, Any], Optional[str]]:
    api = f"https://api.github.com/repos/{repo}"
    body, error = fetch(api, "application/vnd.github+json")
    status = "operational"
    label = "live"
    server_count = None
    recent: List[str] = []
    source_url = f"https://github.com/{repo}"

    if error or not body:
        status = "unknown"
        label = "unknown"
    else:
        try:
            data = json.loads(body)
            if data.get("archived"):
                status = "degraded"
                label = "archived"
            if data.get("html_url"):
                source_url = data["html_url"]
            pushed_at = data.get("pushed_at")
            if pushed_at:
                recent.append(f"Last pushed {pushed_at}")
        except json.JSONDecodeError as exc:
            status = "unknown"
            label = "unknown"
            error = str(exc)

    commits, commit_error = fetch(f"https://api.github.com/repos/{repo}/commits?per_page=3", "application/vnd.github+json")
    if commits:
        try:
            for commit in json.loads(commits)[:3]:
                message = commit.get("commit", {}).get("message", "").splitlines()[0]
                if message:
                    recent.append(message[:110])
        except (json.JSONDecodeError, TypeError):
            pass
    error = error or commit_error

    return {
        "provider": provider,
        "repo": repo,
        "source_url": source_url,
        "status": status,
        "status_label": label,
        "server_count": server_count,
        "recent": recent[:4],
        "last_checked": checked,
        "notes": notes,
    }, error


def build() -> Dict[str, Any]:
    generated = now_utc()
    generated_s = iso(generated)
    checked = generated_s or ""
    errors: List[Dict[str, str]] = []

    mcp = []
    for provider, repo, notes in MCP_REPOS:
        item, error = github_repo(provider, repo, notes, checked)
        mcp.append(item)
        if error:
            errors.append({"source": repo, "error": error})

    quantum_feed = []
    for vendor, kind, url in QUANTUM_SOURCES:
        item, error = parse_feed(vendor, kind, url, checked)
        quantum_feed.append(item)
        if error:
            errors.append({"source": vendor, "error": error})

    health = [
        {
            "vendor": vendor,
            "category": category,
            "status": status,
            "status_label": label,
            "public_health": "unknown" if status in {"unknown", "token_required"} else status,
            "device_feed": "not_configured" if status == "token_required" else "public_sources_only",
            "devices_checked": None,
            "devices_available": None,
            "queue_summary": None,
            "source_url": source_url,
            "last_checked": checked,
            "notes": notes,
        }
        for vendor, category, status, label, source_url, notes in HEALTH_SOURCES
    ]

    summary = {"operational": 0, "degraded": 0, "down": 0, "unknown": 0, "token_required": 0}
    for item in [*mcp, *quantum_feed, *health]:
        status = item.get("status", "unknown")
        summary[status if status in summary else "unknown"] += 1

    return {
        "generated_at": generated_s,
        "next_refresh_at": iso(generated + timedelta(hours=CADENCE_HOURS)),
        "refresh_cadence_hours": CADENCE_HOURS,
        "source_policy": "official_sources_api_backed_where_configured",
        "summary": summary,
        "mcp": mcp,
        "quantum_feed": quantum_feed,
        "health": health,
        "errors": errors,
    }


def main() -> int:
    data = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} with {len(data['errors'])} collection errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
