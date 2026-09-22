"""Phase 5 tests: static site generator (no network, isolated output)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.database import init_db
from core.sitegen import Raw, SiteBuilder, escape, render_template


class TestTemplateEngine(unittest.TestCase):
    def test_escapes_variables(self) -> None:
        out = render_template("Hello {{name}}", {"name": "<script>"})
        self.assertEqual(out, "Hello &lt;script&gt;")

    def test_raw_not_escaped(self) -> None:
        out = render_template("{{body}}", {"body": Raw("<b>ok</b>")})
        self.assertEqual(out, "<b>ok</b>")

    def test_dotted_lookup(self) -> None:
        out = render_template("{{a.b}}", {"a": {"b": "x"}})
        self.assertEqual(out, "x")

    def test_partial_include(self) -> None:
        partials = {"head": "<title>{{t}}</title>"}
        out = render_template(
            "{{>head}}<p>{{t}}</p>", {"t": "Hi"}, partials=partials
        )
        self.assertEqual(out, "<title>Hi</title><p>Hi</p>")

    def test_escape_helper(self) -> None:
        self.assertEqual(escape("a&b"), "a&amp;b")
        self.assertEqual(escape(Raw("<i>")), "<i>")
        self.assertEqual(escape(None), "")


class SiteGenBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "archive.db"
        self.out = self.root / "site"
        self.conn = init_db(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()

    def insert_video(self, video_id: str, **kw) -> None:
        self.conn.execute(
            """
            INSERT INTO video (
                ustaz_id, video_id, title, description,
                playlist_id, playlist_title, playlist_position,
                upload_date, duration, web_url, thumbnail_url,
                state, download_status, classification_status, category,
                series_id,
                first_seen_at, last_seen_at, metadata_hash
            ) VALUES ('ustaz_a', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'GROUPED', 'COMPLETED', 'CLASSIFIED', ?, ?,
                      '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z', 'h')
            """,
            (
                video_id,
                kw.get("title", "Talk"),
                kw.get("description", ""),
                kw.get("playlist_id"),
                kw.get("playlist_title"),
                kw.get("playlist_position"),
                kw.get("upload_date", "2024-01-01"),
                kw.get("duration", 600),
                kw.get("web_url", "https://example.com/v"),
                kw.get("thumbnail_url"),
                kw.get("category"),
                kw.get("series_id"),
            ),
        )
        self.conn.commit()

    def sample_config(self) -> dict:
        return {
            "ustaz": {
                "id": "ustaz_a",
                "identity": {
                    "name": "Example Ustaz",
                    "slug": "example-ustaz",
                    "description": "Talks",
                },
                "site": {
                    "title": "Example Archive",
                    "theme": "default",
                    "base_url": "https://example.org",
                },
                "persona": {
                    "display_name": "Example Ustaz",
                    "short_description": "Talks",
                    "intro_text": "Welcome.",
                },
            }
        }

    def build(self, dry_run: bool = False):
        return SiteBuilder().run(
            "ustaz_a",
            dry_run=dry_run,
            db_path=self.db_path,
            config=self.sample_config(),
            output_dir=self.out,
        )


class TestSiteBuild(SiteGenBase):
    def test_builds_core_pages(self) -> None:
        self.insert_video(
            "aaaaaaaaaaa",
            title="Tafsir Al-Kahf",
            description="About the surah",
            category="tafsir",
            playlist_id="PLabc",
            playlist_title="Tafsir Series",
            playlist_position=1,
            series_id="ser_abc",
        )
        self.conn.execute(
            """
            INSERT INTO series (series_id, ustaz_id, slug, title, description,
                                created_at, updated_at)
            VALUES ('ser_abc', 'ustaz_a', 'tafsir-series', 'Tafsir Series', 'd',
                    '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')
            """
        )
        self.conn.execute(
            "INSERT INTO category (ustaz_id, slug, name) VALUES "
            "('ustaz_a', 'tafsir', 'Tafsir')"
        )
        self.conn.execute(
            """
            INSERT INTO playlist (ustaz_id, playlist_id, title, video_count,
                                  first_seen_at, last_seen_at)
            VALUES ('ustaz_a', 'PLabc', 'Tafsir Series', 1,
                    '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')
            """
        )
        self.conn.commit()

        report = self.build()
        self.assertGreaterEqual(report.pages, 8)
        self.assertEqual(report.videos, 1)
        self.assertEqual(report.categories, 1)
        self.assertEqual(report.series, 1)
        self.assertEqual(report.playlists, 1)

        expected = [
            "index.html",
            "about/index.html",
            "search/index.html",
            "categories/index.html",
            "video/aaaaaaaaaaa/index.html",
            "series/tafsir-series/index.html",
            "playlist/tafsir-series/index.html",
            "category/tafsir/index.html",
            "sitemap.xml",
            "robots.txt",
            "data/search.json",
            "data/videos.json",
            "assets/css/site.css",
            "assets/js/search.js",
        ]
        for rel in expected:
            self.assertTrue((self.out / rel).is_file(), f"missing {rel}")

        home = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn("Example Archive", home)
        # landing shows one card per series — episode titles live on series pages
        self.assertIn("Tafsir Series", home)
        self.assertIn("/series/tafsir-series/", home)
        self.assertIn("1 episode", home)
        self.assertNotIn("Tafsir Al-Kahf", home)
        self.assertNotIn("Latest singles", home)
        self.assertIn('rel="canonical"', home)
        self.assertIn("og:title", home)

        video_page = (
            self.out / "video" / "aaaaaaaaaaa" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Tafsir Al-Kahf", video_page)
        self.assertIn("About the surah", video_page)

        sitemap = (self.out / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("https://example.org/", sitemap)
        self.assertIn("/video/aaaaaaaaaaa/", sitemap)

        robots = (self.out / "robots.txt").read_text(encoding="utf-8")
        self.assertIn("User-agent: *", robots)
        self.assertIn("Sitemap: https://example.org/sitemap.xml", robots)

        search = json.loads((self.out / "data" / "search.json").read_text())
        self.assertEqual(len(search), 1)
        self.assertEqual(search[0]["id"], "aaaaaaaaaaa")
        self.assertEqual(search[0]["category"], "tafsir")

    def test_empty_archive_still_builds_shell(self) -> None:
        report = self.build()
        self.assertGreaterEqual(report.pages, 4)
        self.assertTrue((self.out / "index.html").is_file())
        self.assertTrue((self.out / "sitemap.xml").is_file())
        self.assertEqual(report.videos, 0)
        home = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn("No videos yet.", home)

    def test_landing_groups_series_and_singles(self) -> None:
        self.insert_video(
            "aaaaaaaaaaa", title="Ep 1", category="ceramah", series_id="ser_abc"
        )
        self.insert_video(
            "bbbbbbbbbbb", title="Ep 2", category="ceramah", series_id="ser_abc"
        )
        self.insert_video("ccccccccccc", title="Solo Talk", category="ceramah")
        self.conn.execute(
            """
            INSERT INTO series (series_id, ustaz_id, slug, title, description,
                                created_at, updated_at)
            VALUES ('ser_abc', 'ustaz_a', 'my-series', 'My Series', 'd',
                    '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')
            """
        )
        self.conn.commit()
        self.build()
        home = (self.out / "index.html").read_text(encoding="utf-8")
        # one card for the series with episode count, linking to episodes
        self.assertEqual(home.count("/series/my-series/"), 2)  # title + button
        self.assertIn("2 episodes", home)
        self.assertNotIn("Ep 1", home)
        # ungrouped video listed once under Latest singles
        self.assertIn("Latest singles", home)
        self.assertIn("Solo Talk", home)
        # episodes live on the series page in order
        series_page = (
            self.out / "series" / "my-series" / "index.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Ep 1", series_page)
        self.assertIn("Ep 2", series_page)

    def test_dry_run_writes_nothing(self) -> None:
        self.insert_video("aaaaaaaaaaa", title="Talk", category="ceramah")
        report = self.build(dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertGreater(report.pages, 0)
        self.assertFalse(self.out.exists())

    def test_stale_html_removed_on_rebuild(self) -> None:
        self.insert_video("aaaaaaaaaaa", title="Talk", category="ceramah")
        self.build()
        self.assertTrue((self.out / "video" / "aaaaaaaaaaa" / "index.html").is_file())
        # simulate a pruned video + a retired route left on disk
        self.conn.execute(
            "DELETE FROM video WHERE ustaz_id='ustaz_a' AND video_id='aaaaaaaaaaa'"
        )
        self.conn.commit()
        retired = self.out / "playlists" / "index.html"
        retired.parent.mkdir(parents=True, exist_ok=True)
        retired.write_text("<html></html>", encoding="utf-8")
        keep_me = self.out / "CNAME"
        keep_me.write_text("example.org\n", encoding="utf-8")

        report = self.build()
        self.assertEqual(report.stale_removed, 2)
        self.assertFalse((self.out / "video" / "aaaaaaaaaaa").exists())
        self.assertFalse((self.out / "playlists").exists())
        self.assertTrue((self.out / "index.html").is_file())
        self.assertTrue(keep_me.is_file())  # non-HTML untouched

    def test_dry_run_removes_nothing(self) -> None:
        self.insert_video("aaaaaaaaaaa", title="Talk", category="ceramah")
        self.build()
        stray = self.out / "video" / "zzz" / "index.html"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text("<html></html>", encoding="utf-8")
        report = self.build(dry_run=True)
        self.assertEqual(report.stale_removed, 0)
        self.assertTrue(stray.is_file())

    def test_records_site_build_row(self) -> None:
        report = self.build()
        self.assertEqual(report.ustaz_id, "ustaz_a")
        # SiteBuilder opens its own connection for site_build
        conn2 = init_db(self.db_path)
        try:
            row = conn2.execute(
                "SELECT * FROM site_build WHERE ustaz_id = 'ustaz_a' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn2.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "OK")
        self.assertEqual(row["video_count"], report.videos)


class TestCLIBuild(unittest.TestCase):
    def test_parser_has_build(self) -> None:
        from manager.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["build", "ustaz_a", "--dry-run"])
        self.assertEqual(args.command, "build")
        self.assertTrue(args.dry_run)


if __name__ == "__main__":
    unittest.main()
