"""Phase 6 tests: generated-site validation + publish wiring (no network)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.database import init_db
from core.sitegen import SiteBuilder
from core.siteval import format_report, validate_site


class SiteValBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "archive.db"
        self.out = self.root / "site"
        self.conn = init_db(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()

    def sample_config(self) -> dict:
        return {
            "ustaz": {
                "id": "ustaz_a",
                "identity": {
                    "name": "Example Ustaz",
                    "slug": "example-ustaz",
                    "description": "Talks",
                },
                "site": {"title": "Example Archive", "theme": "default"},
                "persona": {
                    "display_name": "Example Ustaz",
                    "short_description": "Talks",
                    "intro_text": "Welcome.",
                },
            }
        }

    def build(self):
        self.conn.execute(
            """
            INSERT INTO video (
                ustaz_id, video_id, title, description,
                playlist_id, playlist_title, playlist_position,
                upload_date, duration, web_url,
                state, download_status, classification_status, category,
                series_id,
                first_seen_at, last_seen_at, metadata_hash
            ) VALUES ('ustaz_a', 'aaaaaaaaaaa', 'Tafsir Talk', 'About',
                      NULL, NULL, NULL, '2024-01-01', 600,
                      'https://example.com/v',
                      'GROUPED', 'COMPLETED', 'CLASSIFIED', 'ceramah', NULL,
                      '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z', 'h')
            """
        )
        self.conn.execute(
            "INSERT INTO category (ustaz_id, slug, name) VALUES "
            "('ustaz_a', 'ceramah', 'Ceramah')"
        )
        self.conn.commit()
        return SiteBuilder().run(
            "ustaz_a",
            db_path=self.db_path,
            config=self.sample_config(),
            output_dir=self.out,
        )


class TestValidateSite(SiteValBase):
    def test_valid_site_passes(self) -> None:
        self.build()
        report = validate_site(self.out, "ustaz_a")
        self.assertTrue(report.ok, msg="\n".join(report.errors))
        self.assertGreater(report.checked_files, 0)
        self.assertGreater(report.checked_links, 0)

    def test_nojekyll_emitted(self) -> None:
        self.build()
        self.assertTrue((self.out / ".nojekyll").is_file())

    def test_missing_index_fails(self) -> None:
        self.build()
        (self.out / "index.html").unlink()
        report = validate_site(self.out, "ustaz_a")
        self.assertFalse(report.ok)
        self.assertTrue(any("index.html" in e for e in report.errors))

    def test_corrupt_search_json_fails(self) -> None:
        self.build()
        (self.out / "data" / "search.json").write_text("{broken", encoding="utf-8")
        report = validate_site(self.out, "ustaz_a")
        self.assertFalse(report.ok)
        self.assertTrue(any("search.json" in e for e in report.errors))

    def test_broken_internal_link_fails(self) -> None:
        self.build()
        page = self.out / "index.html"
        page.write_text(
            page.read_text(encoding="utf-8") + '<a href="/video/missing123/">x</a>',
            encoding="utf-8",
        )
        report = validate_site(self.out, "ustaz_a")
        self.assertFalse(report.ok)
        self.assertTrue(any("missing123" in e for e in report.errors))

    def test_missing_output_dir_fails(self) -> None:
        report = validate_site(self.root / "nope", "ustaz_a")
        self.assertFalse(report.ok)

    def test_format_report(self) -> None:
        self.build()
        text = format_report(validate_site(self.out, "ustaz_a"))
        self.assertIn("OK", text)


class TestPublishCLI(unittest.TestCase):
    def test_parser_has_publish(self) -> None:
        from manager.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["publish", "ustaz_a", "--dry-run"])
        self.assertEqual(args.command, "publish")
        self.assertTrue(args.dry_run)
        args = parser.parse_args(["publish", "--all"])
        self.assertTrue(args.all)

    def test_publish_registered(self) -> None:
        from manager import cli

        self.assertIn("publish", cli.IMPLEMENTED_STAGES)
        self.assertIn("publish", cli.STAGE_PHASES)
        self.assertEqual(cli.STAGE_PHASES["publish"], 6)


if __name__ == "__main__":
    unittest.main()
