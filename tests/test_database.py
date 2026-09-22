"""Phase 1 tests: SQLite schema init and core tables."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core import database


class TestDatabase(unittest.TestCase):
    def test_init_creates_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "archive.db"
            conn = database.init_db(db_path)
            try:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                ).fetchall()
                names = {r["name"] for r in rows}
                for expected in (
                    "meta",
                    "ustaz",
                    "source",
                    "playlist",
                    "video",
                    "series",
                    "category",
                    "video_series",
                    "video_category",
                    "download_job",
                    "processing_job",
                    "jobs",
                    "error_log",
                    "site_build",
                ):
                    self.assertIn(expected, names)
                version = conn.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()
                self.assertIsNotNone(version)
                self.assertEqual(int(version["value"]), database.SCHEMA_VERSION)
            finally:
                conn.close()

    def test_video_dedup_primary_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "archive.db"
            conn = database.init_db(db_path)
            try:
                conn.execute(
                    "INSERT INTO video (ustaz_id, video_id, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, datetime('now'), datetime('now'))",
                    ("ustaz_a", "abc123"),
                )
                with self.assertRaises(Exception):
                    conn.execute(
                        "INSERT INTO video (ustaz_id, video_id, first_seen_at, last_seen_at) "
                        "VALUES (?, ?, datetime('now'), datetime('now'))",
                        ("ustaz_a", "abc123"),
                    )
                # different ustaz, same video id is allowed (identity is pair)
                conn.execute(
                    "INSERT INTO video (ustaz_id, video_id, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, datetime('now'), datetime('now'))",
                    ("ustaz_b", "abc123"),
                )
                conn.commit()
            finally:
                conn.close()

    def test_video_state_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "archive.db"
            conn = database.init_db(db_path)
            try:
                conn.execute(
                    "INSERT INTO video (ustaz_id, video_id, first_seen_at, last_seen_at) "
                    "VALUES ('u', 'v', datetime('now'), datetime('now'))"
                )
                conn.commit()
                row = conn.execute(
                    "SELECT state, download_status, classification_status FROM video "
                    "WHERE ustaz_id='u' AND video_id='v'"
                ).fetchone()
                self.assertEqual(row["state"], "DISCOVERED")
                self.assertEqual(row["download_status"], "PENDING")
                self.assertEqual(row["classification_status"], "PENDING")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
