"""Phase 3 tests: download queue, state machine, resume, retry (no network)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.database import init_db
from core.download_manager import (
    DownloadManager,
    DownloadReport,
    raw_videos_dir,
    resolve_download_mode,
)
from core.downloader.base import DownloadRequest, DownloadResult, Downloader
from core.rate_limit import RateLimitConfig


class FakeDownloader(Downloader):
    name = "fake"

    def __init__(self, *, fail_ids: set[str] | None = None, write_file: bool = True):
        self.fail_ids = fail_ids or set()
        self.write_file = write_file
        self.calls: list[str] = []

    def download(self, request: DownloadRequest) -> DownloadResult:
        self.calls.append(request.video_id)
        if request.video_id in self.fail_ids:
            return DownloadResult(ok=False, error="simulated network error")
        if request.mode == "metadata":
            return DownloadResult.skip()
        if not self.write_file:
            return DownloadResult(ok=True, path=None)
        request.dest_dir.mkdir(parents=True, exist_ok=True)
        # deterministic filename: <date>_<video_id>_<slug>.mp4
        date = (request.upload_date or "0000-00-00").replace("-", "")
        path = request.dest_dir / f"{date}_{request.video_id}_clip.mp4"
        path.write_bytes(b"fake-media-bytes")
        return DownloadResult(ok=True, path=path)


def _insert_video(conn, ustaz_id: str, video_id: str, **kw) -> None:
    conn.execute(
        """
        INSERT INTO video (
            ustaz_id, video_id, title, original_title, web_url,
            upload_date, state, download_status, classification_status,
            first_seen_at, last_seen_at, metadata_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, 'h')
        """,
        (
            ustaz_id,
            video_id,
            kw.get("title", "Clip"),
            kw.get("title", "Clip"),
            kw.get("web_url", f"https://www.youtube.com/watch?v={video_id}"),
            kw.get("upload_date", "2024-01-15"),
            kw.get("state", "METADATA_READY"),
            kw.get("download_status", "PENDING"),
            "2024-01-01T00:00:00Z",
            "2024-01-01T00:00:00Z",
        ),
    )
    conn.commit()


class DownloadTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "archive.db"
        self.dest = Path(self.tmp.name) / "videos"
        self.conn = init_db(self.db_path)
        # force video mode regardless of sample config
        self._mode = "video"
        self._patch_download_mode()

    def _patch_download_mode(self) -> None:
        import core.download_manager as dm

        self._original_resolve = dm.resolve_download_mode
        dm.resolve_download_mode = lambda u, c: self._mode  # type: ignore[assignment]

    def tearDown(self) -> None:
        import core.download_manager as dm

        dm.resolve_download_mode = self._original_resolve
        self.conn.close()
        self.tmp.cleanup()

    def manager(self, downloader: FakeDownloader | None = None) -> DownloadManager:
        return DownloadManager(
            downloader or FakeDownloader(),
            sleep=lambda _s: None,
            rate_limit=RateLimitConfig(
                source_spacing_seconds=0.0,
                source_spacing_jitter_seconds=0.0,
                cooldown_seconds=0.0,
            ),
        )

    def run_dl(self, downloader: FakeDownloader | None = None, **kwargs) -> DownloadReport:
        kwargs.setdefault("db_path", self.db_path)
        kwargs.setdefault("dest_dir", self.dest)
        return self.manager(downloader).run("ustaz_a", **kwargs)

    def video(self, video_id: str):
        self.conn.rollback()
        return self.conn.execute(
            "SELECT * FROM video WHERE ustaz_id='ustaz_a' AND video_id=?",
            (video_id,),
        ).fetchone()


class TestResolveMode(unittest.TestCase):
    def test_ustaz_mode_wins(self) -> None:
        self.assertEqual(
            resolve_download_mode(
                {"youtube": {"download": True, "audio_only": False}},
                {"download": {"mode": "audio"}},
            ),
            "audio",
        )

    def test_universal_audio(self) -> None:
        self.assertEqual(
            resolve_download_mode(
                {"youtube": {"download": True, "audio_only": True}}, {}
            ),
            "audio",
        )

    def test_universal_download_disabled_uses_metadata(self) -> None:
        self.assertEqual(
            resolve_download_mode({"youtube": {"download": False}}, {}),
            "metadata",
        )


class TestDownloadManager(DownloadTestBase):
    def test_successful_download_completes(self) -> None:
        _insert_video(self.conn, "ustaz_a", "aaaaaaaaaaa")
        fake = FakeDownloader()
        report = self.run_dl(fake)
        self.assertEqual(report.completed, 1)
        self.assertEqual(report.failed, 0)
        row = self.video("aaaaaaaaaaa")
        self.assertEqual(row["download_status"], "COMPLETED")
        self.assertEqual(row["state"], "DOWNLOADED")
        self.assertIsNotNone(row["download_path"])
        self.assertIsNotNone(row["downloaded_at"])
        self.assertIn("aaaaaaaaaaa", row["download_path"])
        job = self.conn.execute(
            "SELECT * FROM download_job WHERE video_id='aaaaaaaaaaa'"
        ).fetchone()
        self.assertEqual(job["status"], "COMPLETED")

    def test_never_redownload_completed(self) -> None:
        _insert_video(self.conn, "ustaz_a", "aaaaaaaaaaa")
        fake = FakeDownloader()
        self.run_dl(fake)
        fake.calls.clear()
        report = self.run_dl(fake)
        self.assertEqual(fake.calls, [])
        self.assertEqual(report.completed, 0)
        self.assertEqual(report.already_done, 1)

    def test_failed_download_sets_failed_after_max_attempts(self) -> None:
        _insert_video(self.conn, "ustaz_a", "aaaaaaaaaaa")
        fake = FakeDownloader(fail_ids={"aaaaaaaaaaa"})
        # universal retry_count default 3 → 3 attempts
        report = self.run_dl(fake)
        # first run processes one claim; remaining retries need repeated runs
        # claim_next leaves PENDING if attempts < max after failure
        while True:
            r2 = self.run_dl(fake)
            if r2.completed == 0 and r2.failed == 0:
                break
            if len(fake.calls) > 10:
                break
        row = self.video("aaaaaaaaaaa")
        job = self.conn.execute(
            "SELECT * FROM download_job WHERE video_id='aaaaaaaaaaa'"
        ).fetchone()
        self.assertEqual(job["status"], "FAILED")
        self.assertEqual(row["download_status"], "FAILED")
        self.assertEqual(row["state"], "FAILED")
        self.assertGreaterEqual(job["attempts"], job["max_attempts"])
        errors = self.conn.execute(
            "SELECT * FROM error_log WHERE video_id='aaaaaaaaaaa'"
        ).fetchall()
        self.assertTrue(errors)

    def test_partial_failure_then_success_on_retry(self) -> None:
        _insert_video(self.conn, "ustaz_a", "aaaaaaaaaaa")
        flaky = FakeDownloader(fail_ids={"aaaaaaaaaaa"})
        self.run_dl(flaky)
        # clear failure and retry-failed
        flaky.fail_ids.clear()
        report = self.run_dl(flaky, retry_failed=True, resume=False)
        self.assertEqual(report.completed, 1)
        row = self.video("aaaaaaaaaaa")
        self.assertEqual(row["download_status"], "COMPLETED")

    def test_resume_reclaims_interrupted(self) -> None:
        _insert_video(
            self.conn,
            "ustaz_a",
            "aaaaaaaaaaa",
            state="DOWNLOADING",
            download_status="DOWNLOADING",
        )
        fake = FakeDownloader()
        report = self.run_dl(fake, resume=True, retry_failed=False)
        self.assertGreaterEqual(report.resumed, 1)
        self.assertEqual(report.completed, 1)
        row = self.video("aaaaaaaaaaa")
        self.assertEqual(row["download_status"], "COMPLETED")

    def test_metadata_mode_skips_media(self) -> None:
        self._mode = "metadata"
        _insert_video(self.conn, "ustaz_a", "aaaaaaaaaaa")
        fake = FakeDownloader()
        report = self.run_dl(fake)
        self.assertEqual(fake.calls, [])
        self.assertEqual(report.skipped, 1)
        row = self.video("aaaaaaaaaaa")
        self.assertEqual(row["download_status"], "SKIPPED")
        self.assertEqual(row["state"], "DOWNLOADED")

    def test_dry_run_no_side_effects(self) -> None:
        _insert_video(self.conn, "ustaz_a", "aaaaaaaaaaa")
        fake = FakeDownloader()
        report = self.run_dl(fake, dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertEqual(report.enqueued, 1)
        self.assertEqual(fake.calls, [])
        row = self.video("aaaaaaaaaaa")
        self.assertEqual(row["download_status"], "PENDING")

    def test_validation_rejects_empty_file(self) -> None:
        _insert_video(self.conn, "ustaz_a", "aaaaaaaaaaa")

        class EmptyFileDownloader(Downloader):
            def download(self, request: DownloadRequest) -> DownloadResult:
                request.dest_dir.mkdir(parents=True, exist_ok=True)
                path = request.dest_dir / (
                    (request.upload_date or "00000000").replace("-", "")
                    + f"_{request.video_id}_x.mp4"
                )
                path.write_bytes(b"")
                return DownloadResult(ok=True, path=path)

        report = self.manager(EmptyFileDownloader()).run(  # type: ignore[arg-type]
            "ustaz_a",
            db_path=self.db_path,
            dest_dir=self.dest,
        )
        self.assertEqual(report.failed, 1)
        self.assertTrue(any("validation" in e or "empty" in e for e in report.errors))
        row = self.video("aaaaaaaaaaa")
        self.assertEqual(row["download_status"], "PENDING")  # attempts left

    def test_validate_file_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "20240115_aaaaaaaaaaa_clip.mp4"
            path.write_bytes(b"data")
            self.assertIsNone(DownloadManager.validate_file(path, "aaaaaaaaaaa"))
            self.assertIsNotNone(
                DownloadManager.validate_file(Path(tmp) / "missing.mp4", "aaaaaaaaaaa")
            )
            empty = Path(tmp) / "20240115_bbbbbbbbbbb_e.mp4"
            empty.write_bytes(b"")
            err = DownloadManager.validate_file(empty, "bbbbbbbbbbb")
            self.assertIsNotNone(err)


class TestDownloadCLI(unittest.TestCase):
    def test_download_command_registered(self) -> None:
        from manager.cli import build_parser

        parser = build_parser()
        for cmd in ("download", "resume", "retry-failed"):
            args = parser.parse_args([cmd, "ustaz_a", "--dry-run", "--fast"])
            self.assertEqual(args.command, cmd)
            self.assertTrue(args.dry_run)

    def test_download_requires_target(self) -> None:
        from manager.cli import main

        self.assertEqual(main(["download"]), 2)


if __name__ == "__main__":
    unittest.main()
