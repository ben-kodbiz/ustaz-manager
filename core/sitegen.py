"""Static site generator (Phase 5). Stdlib-only template engine + page build."""

from __future__ import annotations

import html
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import paths
from core.classifier import slugify
from core.config import ConfigError, load_ustaz_config
from core.database import init_db
from core.logging_setup import log_event


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Raw(str):
    """Pre-rendered HTML fragment — not escaped by the template engine."""


def escape(value: Any) -> str:
    if isinstance(value, Raw):
        return str(value)
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
_PARTIAL_RE = re.compile(r"\{\{>\s*([a-zA-Z0-9_./-]+)\s*\}\}")


def render_template(
    source: str,
    context: dict[str, Any],
    *,
    partials: dict[str, str] | None = None,
    template_dir: Path | None = None,
    _depth: int = 0,
) -> str:
    """Minimal engine: {{var}} (escaped), {{{raw}}} not needed — use Raw values,
    {{>partial}} includes. Nested partials resolved recursively."""
    if _depth > 8:
        raise ValueError("partial include depth exceeded")

    partials = partials if partials is not None else {}

    def load_partial(name: str) -> str:
        if name in partials:
            return partials[name]
        if template_dir is None:
            raise KeyError(f"unknown partial: {name}")
        for candidate in (
            template_dir / "partials" / f"{name}.html",
            template_dir / f"{name}.html",
        ):
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8")
                partials[name] = text
                return text
        raise KeyError(f"unknown partial: {name}")

    def sub_partial(match: re.Match) -> str:
        name = match.group(1)
        body = load_partial(name)
        return render_template(
            body,
            context,
            partials=partials,
            template_dir=template_dir,
            _depth=_depth + 1,
        )

    def lookup(key: str) -> Any:
        node: Any = context
        for part in key.split("."):
            if isinstance(node, dict):
                node = node.get(part, "")
            else:
                return ""
        return node

    def sub_var(match: re.Match) -> str:
        return escape(lookup(match.group(1)))

    # partials first so their variables resolve in the same pass
    out = _PARTIAL_RE.sub(sub_partial, source)
    out = _VAR_RE.sub(sub_var, out)
    return out


@dataclass
class BuildReport:
    ustaz_id: str
    dry_run: bool = False
    pages: int = 0
    videos: int = 0
    categories: int = 0
    series: int = 0
    playlists: int = 0
    stale_removed: int = 0
    output: str = ""
    errors: list[str] = field(default_factory=list)


class SiteBuilder:
    def __init__(self, template_dir: Path | None = None) -> None:
        self.template_dir = template_dir or paths.SITE_TEMPLATE_DIR

    def run(
        self,
        ustaz_id: str,
        *,
        dry_run: bool = False,
        db_path=None,
        config: dict[str, Any] | None = None,
        output_dir: Path | None = None,
    ) -> BuildReport:
        report = BuildReport(ustaz_id=ustaz_id, dry_run=dry_run)

        if config is None:
            try:
                merged = load_ustaz_config(ustaz_id)
            except ConfigError as exc:
                raise ConfigError(str(exc)) from exc
            ustaz_cfg = merged["ustaz"]
        else:
            ustaz_cfg = config.get("ustaz") or config

        site_cfg = ustaz_cfg.get("site") or {}
        persona = ustaz_cfg.get("persona") or {}
        identity = ustaz_cfg.get("identity") or {}

        site_title = site_cfg.get("title") or f"{identity.get('name', ustaz_id)} Archive"
        base_url = str(site_cfg.get("base_url") or "").rstrip("/")
        display_name = persona.get("display_name") or identity.get("name") or ustaz_id
        description = (
            persona.get("short_description")
            or identity.get("description")
            or "Archive of lectures and educational talks."
        )
        intro = persona.get("intro_text") or description

        if output_dir is None:
            output_dir = paths.REPO_ROOT / "apps" / ustaz_id

        if not self.template_dir.is_dir():
            report.errors.append(f"missing template dir: {self.template_dir}")
            return report

        conn = init_db(db_path)
        try:
            data = self._collect(conn, ustaz_id)
        finally:
            conn.close()

        report.videos = len(data["videos"])
        report.categories = len(data["categories"])
        report.series = len(data["series"])
        report.playlists = len(data["playlists"])

        root_prefix = self._root_prefix_for_pages()
        nav = Raw(
            self._nav_html(root_prefix)
        )

        common = {
            "site_title": site_title,
            "site_description": description,
            "display_name": display_name,
            "intro_text": intro,
            "ustaz_id": ustaz_id,
            "base_url": base_url,
            "video_count": str(report.videos),
            "category_count": str(report.categories),
            "series_count": str(report.series),
            "playlist_count": str(report.playlists),
            "generated_at": _utcnow(),
            "root": root_prefix,
            "nav": nav,
            "asset_css": f"{root_prefix}assets/css/site.css",
            "asset_js": f"{root_prefix}assets/js/search.js",
            "canonical_base": (base_url or "") + "/",
        }

        pages: list[tuple[str, dict[str, Any]]] = []

        # index — one card per series (unique titles); click through to episodes.
        # Ungrouped videos appear under "Latest singles". No episode is ever
        # listed twice on the landing page.
        pages.append(
            (
                "index.html",
                {
                    **common,
                    "page_title": site_title,
                    "page_description": description,
                    "page_path": "/",
                    "content": Raw(
                        self._landing_html(
                            data,
                            root_prefix,
                            display_name=display_name,
                        )
                    ),
                    "section_title": "Browse the archive",
                },
            )
        )

        # about
        pages.append(
            (
                "about/index.html",
                {
                    **common,
                    "page_title": f"About — {site_title}",
                    "page_description": description,
                    "page_path": "/about/",
                    "content": Raw(
                        f"<p class=\"lead\">{html.escape(intro)}</p>"
                        f"<p>{report.videos} videos · {report.categories} categories · "
                        f"{report.series} series · {report.playlists} playlists</p>"
                    ),
                    "section_title": "About",
                },
            )
        )

        # search
        pages.append(
            (
                "search/index.html",
                {
                    **common,
                    "page_title": f"Search — {site_title}",
                    "page_description": f"Search the archive of {display_name}.",
                    "page_path": "/search/",
                    "content": Raw(
                        "<label for=\"q\">Search</label>"
                        "<input type=\"search\" id=\"q\" placeholder=\"Search videos…\" "
                        "autocomplete=\"off\">"
                        "<div id=\"results\" aria-live=\"polite\"></div>"
                    ),
                    "section_title": "Search",
                    "search_js": True,
                },
            )
        )

        # categories index + detail
        pages.append(
            (
                "categories/index.html",
                {
                    **common,
                    "page_title": f"Categories — {site_title}",
                    "page_description": f"Browse categories for {display_name}.",
                    "page_path": "/categories/",
                    "content": Raw(self._category_list(data["categories"], root_prefix)),
                    "section_title": "Categories",
                },
            )
        )
        for cat in data["categories"]:
            vids = data["videos_by_category"].get(cat["slug"], [])
            pages.append(
                (
                    f"category/{cat['slug']}/index.html",
                    {
                        **common,
                        "page_title": f"{cat['name']} — {site_title}",
                        "page_description": f"Videos in category {cat['name']}.",
                        "page_path": f"/category/{cat['slug']}/",
                        "content": Raw(self._video_cards(vids, root_prefix)),
                        "section_title": cat["name"],
                    },
                )
            )

        # playlist detail pages (no /playlists/ index — landing covers browsing)
        for pl in data["playlists"]:
            vids = data["videos_by_playlist"].get(pl["playlist_id"], [])
            pages.append(
                (
                    f"playlist/{pl['slug']}/index.html",
                    {
                        **common,
                        "page_title": f"{pl['title']} — {site_title}",
                        "page_description": pl.get("title") or "Playlist",
                        "page_path": f"/playlist/{pl['slug']}/",
                        "content": Raw(self._video_cards(vids, root_prefix)),
                        "section_title": pl["title"],
                    },
                )
            )

        # series detail pages (no /series/ index — landing covers browsing)
        for ser in data["series"]:
            vids = data["videos_by_series"].get(ser["series_id"], [])
            pages.append(
                (
                    f"series/{ser['slug']}/index.html",
                    {
                        **common,
                        "page_title": f"{ser['title']} — {site_title}",
                        "page_description": ser.get("description") or ser["title"],
                        "page_path": f"/series/{ser['slug']}/",
                        "content": Raw(self._video_cards(vids, root_prefix)),
                        "section_title": ser["title"],
                    },
                )
            )

        # video detail pages
        for video in data["videos"]:
            pages.append(
                (
                    f"video/{video['video_id']}/index.html",
                    {
                        **common,
                        "page_title": f"{video['title']} — {site_title}",
                        "page_description": (video.get("description") or video["title"])[:300],
                        "page_path": f"/video/{video['video_id']}/",
                        "content": Raw(self._video_detail(video, root_prefix, data)),
                        "section_title": video["title"],
                    },
                )
            )

        page_template = (self.template_dir / "page.html").read_text(encoding="utf-8")

        written: list[str] = []
        written_files: set[str] = set()
        for rel_path, ctx in pages:
            # engine has no conditionals — inject scripts via Raw slot
            scripts = ""
            if ctx.pop("search_js", False):
                scripts = (
                    f'<script src="{ctx.get("asset_js", "/assets/js/search.js")}" '
                    "defer></script>"
                )
            ctx = {**ctx, "scripts": Raw(scripts)}
            html_out = render_template(
                page_template,
                ctx,
                template_dir=self.template_dir,
            )
            written.append("/" + rel_path.replace("index.html", ""))
            written_files.add(rel_path)
            if dry_run:
                report.pages += 1
                continue
            dest = output_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(html_out, encoding="utf-8")
            report.pages += 1

        # JSON data for client-side search
        search_payload = [
            {
                "id": v["video_id"],
                "title": v["title"],
                "description": (v.get("description") or "")[:500],
                "category": v.get("category") or "",
                "series": v.get("series_title") or "",
                "playlist": v.get("playlist_title") or "",
                "url": f"video/{v['video_id']}/",
            }
            for v in data["videos"]
        ]
        videos_payload = [
            {
                "video_id": v["video_id"],
                "title": v["title"],
                "description": v.get("description"),
                "category": v.get("category"),
                "series_id": v.get("series_id"),
                "series_title": v.get("series_title"),
                "playlist_id": v.get("playlist_id"),
                "playlist_title": v.get("playlist_title"),
                "upload_date": v.get("upload_date"),
                "duration": v.get("duration"),
                "thumbnail_url": v.get("thumbnail_url"),
                "web_url": v.get("web_url"),
            }
            for v in data["videos"]
        ]

        sitemap_paths = sorted(set(written))
        sitemap = self._sitemap_xml(sitemap_paths, base_url)
        robots = self._robots_txt(base_url)

        if not dry_run:
            data_dir = output_dir / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "search.json").write_text(
                json.dumps(search_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (data_dir / "videos.json").write_text(
                json.dumps(videos_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            (output_dir / "sitemap.xml").write_text(sitemap, encoding="utf-8")
            (output_dir / "robots.txt").write_text(robots, encoding="utf-8")
            # GitHub Pages: disable Jekyll so underscore dirs/assets serve as-is
            (output_dir / ".nojekyll").write_text("", encoding="utf-8")
            assets_src = self.template_dir / "assets"
            assets_dst = output_dir / "assets"
            if assets_src.is_dir():
                if assets_dst.exists():
                    shutil.rmtree(assets_dst)
                shutil.copytree(assets_src, assets_dst)
            # reconcile: drop HTML this build did not emit (pruned videos,
            # regrouped-away slugs, retired routes), then drop emptied dirs
            report.stale_removed = self._remove_stale_html(output_dir, written_files)
            # count static artifacts as pages in report? keep HTML only
            report.pages = report.pages  # already counted HTML

        report.output = str(output_dir)
        report.pages += 0  # HTML already counted

        if not dry_run:
            self._record_build(
                ustaz_id, "OK", output_dir, report.videos, None, db_path=db_path
            )
            log_event(
                "build",
                f"built {report.pages} pages → {output_dir}",
                ustaz_id=ustaz_id,
            )
        return report

    @staticmethod
    def _remove_stale_html(output_dir: Path, keep: set[str]) -> int:
        """Delete *.html under output_dir not emitted by this build.

        Generated output is fully derived from the DB each run, so any HTML
        file outside this run's page set is stale (pruned videos, moved
        series, retired routes). Non-HTML files (CNAME, JSON, assets) are
        never touched. Returns the removed count.
        """
        removed = 0
        for html_file in sorted(output_dir.rglob("*.html")):
            if html_file.relative_to(output_dir).as_posix() not in keep:
                html_file.unlink()
                removed += 1
        for child in sorted(output_dir.rglob("*"), reverse=True):
            if child.is_dir():
                try:
                    child.rmdir()  # only succeeds when empty
                except OSError:
                    pass
        return removed

    # --- data collection -------------------------------------------------

    def _collect(self, conn, ustaz_id: str) -> dict[str, Any]:
        video_rows = conn.execute(
            """
            SELECT v.*, s.title AS series_title, s.slug AS series_slug
            FROM video v
            LEFT JOIN series s ON s.series_id = v.series_id
            WHERE v.ustaz_id = ?
            ORDER BY COALESCE(v.upload_date, '') DESC, v.video_id
            """,
            (ustaz_id,),
        ).fetchall()

        videos: list[dict[str, Any]] = []
        for row in video_rows:
            item = dict(row)
            item["title"] = item.get("title") or item.get("original_title") or item["video_id"]
            videos.append(item)

        cat_rows = conn.execute(
            "SELECT slug, name FROM category WHERE ustaz_id = ? ORDER BY name",
            (ustaz_id,),
        ).fetchall()
        categories = [dict(r) for r in cat_rows]

        series_rows = conn.execute(
            "SELECT series_id, slug, title, description, category FROM series "
            "WHERE ustaz_id = ? ORDER BY title",
            (ustaz_id,),
        ).fetchall()
        series = [dict(r) for r in series_rows]

        pl_rows = conn.execute(
            """
            SELECT playlist_id, title, video_count FROM playlist
            WHERE ustaz_id = ? ORDER BY title
            """,
            (ustaz_id,),
        ).fetchall()
        playlists = []
        for r in pl_rows:
            item = dict(r)
            item["title"] = item.get("title") or item["playlist_id"]
            item["slug"] = slugify(item["title"])
            playlists.append(item)

        # also derive playlists present only on videos
        seen_pl = {p["playlist_id"] for p in playlists}
        for v in videos:
            pid = v.get("playlist_id")
            if pid and pid not in seen_pl:
                title = v.get("playlist_title") or pid
                playlists.append(
                    {
                        "playlist_id": pid,
                        "title": title,
                        "video_count": sum(
                            1 for x in videos if x.get("playlist_id") == pid
                        ),
                        "slug": slugify(title),
                    }
                )
                seen_pl.add(pid)

        videos_by_category: dict[str, list[dict]] = {}
        for v in videos:
            cat = v.get("category")
            if cat:
                videos_by_category.setdefault(slugify(cat), []).append(v)

        videos_by_series: dict[str, list[dict]] = {}
        for v in videos:
            sid = v.get("series_id")
            if sid:
                videos_by_series.setdefault(sid, []).append(v)
        for sid, vids in videos_by_series.items():
            vids.sort(
                key=lambda x: (
                    x.get("playlist_position") is None,
                    x.get("playlist_position") or 0,
                    x.get("upload_date") or "",
                )
            )

        videos_by_playlist: dict[str, list[dict]] = {}
        for v in videos:
            pid = v.get("playlist_id")
            if pid:
                videos_by_playlist.setdefault(pid, []).append(v)
        for pid, vids in videos_by_playlist.items():
            vids.sort(
                key=lambda x: (
                    x.get("playlist_position") is None,
                    x.get("playlist_position") or 0,
                )
            )

        return {
            "videos": videos,
            "categories": categories,
            "series": series,
            "playlists": playlists,
            "videos_by_category": videos_by_category,
            "videos_by_series": videos_by_series,
            "videos_by_playlist": videos_by_playlist,
        }

    # --- HTML fragments --------------------------------------------------

    @staticmethod
    def _root_prefix_for_pages() -> str:
        # All pages are served as directory indexes; absolute-from-root paths
        # keep assets/nav working at any depth when hosted at site root.
        return "/"

    @staticmethod
    def _nav_html(root: str) -> str:
        items = [
            ("/", "Home"),
            (f"{root}categories/", "Categories"),
            (f"{root}search/", "Search"),
            (f"{root}about/", "About"),
        ]
        # root already has leading slash for asset-style; home is root itself
        links = []
        for href, label in items:
            if href == "/":
                links.append(f'<a href="/">{html.escape(label)}</a>')
            else:
                links.append(f'<a href="{html.escape(href)}">{html.escape(label)}</a>')
        return "<nav aria-label=\"Main\">" + "".join(links) + "</nav>"

    def _landing_html(
        self, data: dict[str, Any], root: str, *, display_name: str
    ) -> str:
        videos = data["videos"]
        series = data["series"]
        by_series = data["videos_by_series"]
        parts: list[str] = []

        hero_stats = (
            f"{len(videos)} videos · {len(data['categories'])} categories · "
            f"{len(series)} series · {len(data['playlists'])} playlists"
        )
        parts.append(
            '<section class="hero"><p class="hero-kicker">'
            f"{html.escape(display_name)}</p>"
            f'<p class="hero-stats">{html.escape(hero_stats)}</p></section>'
        )

        if series:
            ranked = []
            for s in series:
                vids = by_series.get(s["series_id"], [])
                latest = max(
                    (v.get("upload_date") or "" for v in vids), default=""
                )
                ranked.append((latest, s, len(vids)))
            ranked.sort(key=lambda item: item[0], reverse=True)
            cards = []
            for latest, s, count in ranked:
                href = f"{root}series/{html.escape(s['slug'])}/"
                title = html.escape(s["title"])
                noun = "episode" if count == 1 else "episodes"
                meta = f"{count} {noun}"
                if latest:
                    meta += f" · latest {html.escape(latest)}"
                cards.append(
                    '<article class="card series-card">'
                    f'<h3><a href="{href}">{title}</a></h3>'
                    f'<p class="meta">{meta}</p>'
                    f'<p><a class="button" href="{href}">Open series</a></p>'
                    "</article>"
                )
            parts.append(
                '<h2 class="section-title">Series</h2>'
                '<div class="grid">' + "".join(cards) + "</div>"
            )

        singles = [v for v in videos if not v.get("series_id")][:12]
        if singles:
            parts.append(
                '<h2 class="section-title">Latest singles</h2>'
                + self._video_cards(singles, root)
            )

        if len(parts) == 1:  # hero only — nothing catalogued yet
            parts.append('<p class="empty">No videos yet.</p>')
        return "".join(parts)

    @staticmethod
    def _video_cards(videos: list[dict[str, Any]], root: str) -> str:
        if not videos:
            return "<p class=\"empty\">No videos yet.</p>"
        cards = []
        for v in videos:
            title = html.escape(v["title"])
            href = f"{root}video/{html.escape(v['video_id'])}/"
            cat = v.get("category") or ""
            cat_html = (
                f'<span class="tag">{html.escape(str(cat))}</span>' if cat else ""
            )
            date = html.escape(v.get("upload_date") or "")
            cards.append(
                f'<article class="card"><h3><a href="{href}">{title}</a></h3>'
                f'<p class="meta">{date} {cat_html}</p></article>'
            )
        return '<div class="grid">' + "".join(cards) + "</div>"

    @staticmethod
    def _category_list(categories: list[dict], root: str) -> str:
        if not categories:
            return "<p class=\"empty\">No categories yet.</p>"
        items = []
        for c in categories:
            href = f"{root}category/{html.escape(c['slug'])}/"
            items.append(
                f'<li><a href="{href}">{html.escape(c["name"])}</a></li>'
            )
        return '<ul class="list">' + "".join(items) + "</ul>"

    def _video_detail(
        self, video: dict[str, Any], root: str, data: dict[str, Any]
    ) -> str:
        parts: list[str] = []
        parts.append(f"<h2>{html.escape(video['title'])}</h2>")
        meta = []
        if video.get("upload_date"):
            meta.append(html.escape(str(video["upload_date"])))
        if video.get("duration"):
            meta.append(f"{int(video['duration']) // 60} min")
        if video.get("category"):
            cat = video["category"]
            meta.append(
                f'<a class="tag" href="{root}category/{html.escape(slugify(str(cat)))}/">'
                f"{html.escape(str(cat))}</a>"
            )
        if video.get("series_title"):
            sid = video.get("series_id") or ""
            slug = video.get("series_slug") or slugify(str(video["series_title"]))
            meta.append(
                f'<a href="{root}series/{html.escape(slug)}/">'
                f'{html.escape(str(video["series_title"]))}</a>'
            )
        if video.get("playlist_title"):
            meta.append(html.escape(str(video["playlist_title"])))
        if meta:
            parts.append('<p class="meta">' + " · ".join(meta) + "</p>")

        if video.get("thumbnail_url"):
            thumb = html.escape(str(video["thumbnail_url"]))
            parts.append(f'<img class="thumb" src="{thumb}" alt="" loading="lazy">')

        if video.get("description"):
            # preserve paragraphs, escape the rest
            desc = html.escape(str(video["description"]))
            paragraphs = "".join(
                f"<p>{p.strip()}</p>"
                for p in re.split(r"\n\s*\n", desc)
                if p.strip()
            )
            parts.append(f'<div class="description">{paragraphs}</div>')

        if video.get("web_url"):
            parts.append(
                f'<p><a class="button" rel="noopener" '
                f'href="{html.escape(str(video["web_url"]))}">Watch on source</a></p>'
            )
        return "".join(parts)

    # --- sitemap / robots ------------------------------------------------

    @staticmethod
    def _xml_escape(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    def _sitemap_xml(self, page_paths: list[str], base_url: str) -> str:
        today = _utcnow()[:10]
        urls = []
        for path in page_paths:
            if base_url:
                loc = self._xml_escape(base_url + path)
            else:
                loc = self._xml_escape(path)
            urls.append(
                f"  <url><loc>{loc}</loc><lastmod>{today}</lastmod></url>"
            )
        body = "\n".join(urls)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{body}\n</urlset>\n"
        )

    def _robots_txt(self, base_url: str) -> str:
        lines = ["User-agent: *", "Allow: /"]
        if base_url:
            lines.append(f"Sitemap: {base_url}/sitemap.xml")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _record_build(
        ustaz_id: str,
        status: str,
        output_dir: Path,
        video_count: int,
        error: str | None,
        *,
        db_path=None,
    ) -> None:
        conn = init_db(db_path)
        try:
            now = _utcnow()
            conn.execute(
                """
                INSERT INTO site_build
                    (ustaz_id, status, output_path, video_count, error,
                     started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ustaz_id,
                    status,
                    str(output_dir),
                    video_count,
                    error,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
