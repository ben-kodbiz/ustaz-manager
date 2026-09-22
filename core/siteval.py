"""Generated-site validation (Phase 6). Stdlib-only checks over apps/<id>/.

Validates what `SiteBuilder` emitted before git commit / Pages deploy:
required pages, sitemap/robots, JSON payloads, .nojekyll, and that every
root-absolute internal link (href="/...") resolves to a file on disk.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIRED_PAGES = (
    "index.html",
    "about/index.html",
    "search/index.html",
    "categories/index.html",
)

REQUIRED_FILES = REQUIRED_PAGES + (
    "sitemap.xml",
    "robots.txt",
    "data/search.json",
    "data/videos.json",
    ".nojekyll",
)

# Detail pages (series/<slug>/, playlist/<slug>/) are discovered by walking
# the output tree — only index-level pages are enumerated above.

_HREF_RE = re.compile(r"""href\s*=\s*["'](/[^"'#?]*)["']""", re.IGNORECASE)
_SRC_RE = re.compile(r"""\ssrc\s*=\s*["'](/[^"'#?]*)["']""", re.IGNORECASE)


@dataclass
class ValidationReport:
    ustaz_id: str
    ok: bool = False
    checked_files: int = 0
    checked_links: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _resolve_root_link(output_dir: Path, href: str) -> Path | None:
    """Map a root-absolute href to a file under output_dir (dir-index aware)."""
    rel = href.lstrip("/")
    candidate = output_dir / rel
    if candidate.is_file():
        return candidate
    if candidate.is_dir() and (candidate / "index.html").is_file():
        return candidate / "index.html"
    # "/foo" may mean "/foo/index.html" even when dir listing differs
    if (output_dir / rel / "index.html").is_file():
        return output_dir / rel / "index.html"
    return None


def validate_site(output_dir: Path, ustaz_id: str = "") -> ValidationReport:
    report = ValidationReport(ustaz_id=ustaz_id)
    output_dir = Path(output_dir)

    if not output_dir.is_dir():
        report.errors.append(f"missing output dir: {output_dir}")
        return report

    for rel in REQUIRED_FILES:
        report.checked_files += 1
        if not (output_dir / rel).is_file():
            report.errors.append(f"missing required file: {rel}")

    # sitemap must be well-formed XML with at least one <url>
    sitemap = output_dir / "sitemap.xml"
    if sitemap.is_file():
        text = sitemap.read_text(encoding="utf-8", errors="replace")
        if "<urlset" not in text or "<url>" not in text and "<url " not in text:
            report.errors.append("sitemap.xml has no <url> entries")
        for loc in re.findall(r"<loc>(.*?)</loc>", text):
            loc = loc.strip()
            if loc.startswith("/"):
                report.checked_links += 1
                if _resolve_root_link(output_dir, loc.split("?")[0]) is None:
                    report.errors.append(f"sitemap loc missing on disk: {loc}")

    robots = output_dir / "robots.txt"
    if robots.is_file():
        text = robots.read_text(encoding="utf-8", errors="replace")
        if "User-agent:" not in text:
            report.errors.append("robots.txt missing User-agent line")

    for name in ("data/search.json", "data/videos.json"):
        path = output_dir / name
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            report.errors.append(f"{name} invalid JSON: {exc}")
            continue
        if not isinstance(payload, list):
            report.errors.append(f"{name} must be a JSON list")
        elif name == "data/search.json":
            for i, item in enumerate(payload):
                if not isinstance(item, dict) or not item.get("id"):
                    report.errors.append(
                        f"{name}[{i}] missing id"
                    )
                    break

    # internal link integrity across every HTML page
    html_files = sorted(output_dir.rglob("*.html"))
    if not html_files:
        report.errors.append("no .html pages found")
    for html_file in html_files:
        try:
            text = html_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            report.errors.append(f"unreadable {html_file.name}: {exc}")
            continue
        rel_page = html_file.relative_to(output_dir).as_posix()
        for pattern in (_HREF_RE, _SRC_RE):
            for href in pattern.findall(text):
                if href.startswith(("/assets/", "/about/", "/search/",
                                     "/categories/", "/category/", "/playlists/",
                                     "/playlist/", "/series/", "/video/", "/")):
                    report.checked_links += 1
                    target = href.split("#")[0].split("?")[0]
                    if target in ("/", ""):
                        continue
                    if target.startswith("/data/"):
                        if not (output_dir / target.lstrip("/")).is_file():
                            report.errors.append(
                                f"{rel_page}: broken asset link {href}"
                            )
                        continue
                    if target.endswith((".css", ".js", ".json", ".xml", ".txt")):
                        if not (output_dir / target.lstrip("/")).is_file():
                            report.errors.append(
                                f"{rel_page}: broken static link {href}"
                            )
                        continue
                    if _resolve_root_link(output_dir, target) is None:
                        report.errors.append(
                            f"{rel_page}: broken internal link {href}"
                        )

    # external absolute filesystem paths must never leak into output
    for html_file in html_files:
        try:
            text = html_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "/mnt/" in text or "/home/" in text or "C:\\" in text:
            report.warnings.append(
                f"{html_file.relative_to(output_dir).as_posix()}: "
                "possible absolute filesystem path in HTML"
            )

    report.ok = not report.errors
    return report


def validate_ustaz_output(
    ustaz_id: str,
    *,
    output_dir: Path | None = None,
) -> ValidationReport:
    """Resolve apps/<id>/ from the repo layout and validate it."""
    from core import paths

    if output_dir is None:
        output_dir = paths.REPO_ROOT / "apps" / ustaz_id
    return validate_site(output_dir, ustaz_id)


def format_report(report: ValidationReport) -> str:
    status = "OK" if report.ok else "FAILED"
    lines = [
        f"site validation {status} for '{report.ustaz_id}': "
        f"files={report.checked_files} links={report.checked_links} "
        f"errors={len(report.errors)} warnings={len(report.warnings)}"
    ]
    for err in report.errors[:20]:
        lines.append(f"  error: {err}")
    for warn in report.warnings[:10]:
        lines.append(f"  warning: {warn}")
    return "\n".join(lines)
