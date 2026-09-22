"""Phase 7 tests: read-only local dashboard over SQLite (no network)."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from core.database import init_db
from manager.dashboard import collect_dashboard, format_dashboard


@dataclass
class FakeEntry:
    id: str
    name: str
    enabled: bool = True


class DashboardBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "archive.db"
        self.conn = init_db(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()

    def insert_video(self, video_id: str, **kw) -> None:
        self.conn.execute(
            """
            INSERT INTO video (
                ustaz_id, video_id, title,
                state, download_status, classification_status,
                last_seen_at, first_seen_at
            ) VALUES ('ustaz_a', ?, 'Talk', ?, ?, 'PENDING', ?, '2024-01-01T00:00:00Z')
            """,
            (
                video_id,
                kw.get("state", "METADATA_READY"),
                kw.get("download_status", "PENDING"),
                kw.get("last_seen_at", "2024-05-01T00:00:00Z"),
            ),
        )
        self.conn.commit()


class TestCollectDashboard(DashboardBase):
    def test_counts_and_build_status(self) -> None:
        self.insert_video("aaaaaaaaaaa", download_status="COMPLETED", state="DOWNLOADED")
        self.insert_video("bbbbbbbbbbb", download_status="FAILED", state="FAILED")
        self.insert_video("ccccccccccc")  # PENDING
        self.conn.execute(
            "INSERT INTO series (series_id, ustaz_id, slug, title, created_at, updated_at)"
            " VALUES ('ser_x', 'ustaz_a', 's', 'S', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')"
        )
        self.conn.execute(
            "INSERT INTO site_build (ustaz_id, status, output_path, video_count,"
            " started_at, finished_at)"
            " VALUES ('ustaz_a', 'OK', 'apps/ustaz_a', 3,"
            " '2024-05-01T00:00:00Z', '2024-05-01T00:05:00Z')"
        )
        self.conn.execute(
            "INSERT INTO error_log (ustaz_id, stage, message, created_at)"
            " VALUES ('ustaz_a', 'download', 'boom', '2024-05-01T00:01:00Z')"
        )
        self.conn.commit()

        dashboard = collect_dashboard(self.conn, [FakeEntry("ustaz_a", "Example")])
        self.assertEqual(len(dashboard.rows), 1)
        row = dashboard.rows[0]
        self.assertEqual(row.videos, 3)
        self.assertEqual(row.pending, 1)
        self.assertEqual(row.failed, 1)
        self.assertEqual(row.downloaded, 1)
        self.assertEqual(row.series, 1)
        self.assertEqual(row.last_sync, "2024-05-01T00:00:00Z")
        self.assertEqual(row.build_status, "OK")
        self.assertEqual(row.build_videos, 3)
        self.assertEqual(len(dashboard.recent_errors), 1)

    def test_empty_ustaz_shows_dashes(self) -> None:
        dashboard = collect_dashboard(self.conn, [FakeEntry("ustaz_a", "Example")])
        row = dashboard.rows[0]
        self.assertEqual(row.videos, 0)
        self.assertEqual(row.build_status, "-")
        self.assertEqual(row.last_sync, "-")
        self.assertEqual(dashboard.recent_errors, [])

    def test_is_readonly(self) -> None:
        self.insert_video("aaaaaaaaaaa")
        before = self.conn.execute("SELECT count(*) FROM video").fetchone()[0]
        collect_dashboard(self.conn, [FakeEntry("ustaz_a", "Example")])
        after = self.conn.execute("SELECT count(*) FROM video").fetchone()[0]
        self.assertEqual(before, after)

    def test_format_contains_rows_and_failures(self) -> None:
        self.insert_video("aaaaaaaaaaa", download_status="FAILED", state="FAILED")
        self.conn.execute(
            "INSERT INTO error_log (ustaz_id, stage, message, created_at)"
            " VALUES ('ustaz_a', 'download', 'boom', '2024-05-01T00:01:00Z')"
        )
        self.conn.commit()
        text = format_dashboard(
            collect_dashboard(self.conn, [FakeEntry("ustaz_a", "Example")])
        )
        self.assertIn("Archive Manager", text)
        self.assertIn("ustaz_a", text)
        self.assertIn("Recent failures:", text)
        self.assertIn("boom", text)


class TestDashboardCLI(unittest.TestCase):
    def test_parser_has_dashboard(self) -> None:
        from manager.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["dashboard"])
        self.assertEqual(args.command, "dashboard")
        args = parser.parse_args(["dashboard", "ustaz_a"])
        self.assertEqual(args.ustaz_id, "ustaz_a")

    def test_dashboard_exit_zero_readonly(self) -> None:
        from manager.cli import main

        self.assertEqual(main(["dashboard"]), 0)
        self.assertEqual(main(["dashboard", "ustaz_a"]), 0)

    def test_dashboard_unknown_id(self) -> None:
        from manager.cli import main

        self.assertEqual(main(["dashboard", "no_such_ustaz"]), 1)


if __name__ == "__main__":
    unittest.main()
