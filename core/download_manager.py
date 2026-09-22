"""Download manager: SQLite queue + video state machine (Phase 3).

States (video.state):
  METADATA_READY/QUEUED → DOWNLOADING → DOWNLOADED
                       ↘ FAILED → (retry) → QUEUED

download_status: PENDING | DOWNLOADING | COMPLETED | FAILED | SKIPPED

SQLite is the source of truth. Completed videos are never redownloaded.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core import paths
from core.config import ConfigError, load_ustaz_config
from core.database import init_db
from core.downloader.base import DownloadRequest, DownloadResult, Downloader
from core.downloader.ytdlp import YtDlpDownloader
from core.logging_setup import log_event
from core.rate_limit import RateLimitConfig, is_throttle_signal, source_delay

VALID_DOWNLOAD_STATUSES = frozenset(
    {"PENDING", "DOWNLOADING", "COMPLETED", "FAILED", "SKIPPED"}
)
VALID_STATES = frozenset(
    {
        "DISCOVERED",
        "METADATA_READY",
        "QUEUED",
        "DOWNLOADING",
        "FAILED",
        "DOWNLOADED",
        "CLASSIFIED",
        "GROUPED",
        "INDEXED",
        "PUBLISHED",
    }
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class DownloadReport:
    ustaz_id: str
    dry_run: bool = False
    mode: str = "video"
    enqueued: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    resumed: int = 0
    already_done: int = 0
    errors: list[str] = field(default_factory=list)


def resolve_download_mode(universal: dict, ustaz_cfg: dict) -> str:
    """Per-ustaz download.mode wins; else derive from universal youtube flags."""
    mode = ((ustaz_cfg.get("download") or {}).get("mode") or "").lower()
    if mode in ("metadata", "audio", "video"):
        return mode
    if not universal.get("youtube", {}).get("download", True):
        return "metadata"
    if universal.get("youtube", {}).get("audio_only", False):
        return "audio"
    return "video"


def raw_videos_dir(ustaz_id: str) -> Path:
    return paths.RAW_DIR / ustaz_id / "videos"


class DownloadManager:
    def __init__(
        self,
        downloader: Downloader | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] | None = None,
        rate_limit: RateLimitConfig | None = None,
    ):
        self.downloader = downloader or YtDlpDownloader()
        self._sleep = sleep
        self._rng = rng
        self.rate_limit = rate_limit

    # ── queue helpers ──────────────────────────────────────────────

    @staticmethod
    def reclaim_interrupted(conn, ustaz_id: str) -> int:
        """Stale DOWNLOADING rows → PENDING (process is not running)."""
        reclaimed = 0
        cur = conn.execute(
            """
            UPDATE download_job
            SET status = 'PENDING', started_at = NULL, error = NULL
            WHERE ustaz_id = ? AND status = 'DOWNLOADING'
            """,
            (ustaz_id,),
        )
        reclaimed += cur.rowcount or 0
        cur = conn.execute(
            """
            UPDATE video
            SET state = CASE
                    WHEN state = 'DOWNLOADING' THEN 'QUEUED'
                    ELSE state
                END,
                download_status = CASE
                    WHEN download_status = 'DOWNLOADING' THEN 'PENDING'
                    ELSE download_status
                END
            WHERE ustaz_id = ? AND download_status = 'DOWNLOADING'
            """,
            (ustaz_id,),
        )
        reclaimed += cur.rowcount or 0
        conn.commit()
        return reclaimed

    @staticmethod
    def reset_failed_for_retry(conn, ustaz_id: str, *, video_ids: list[str] | None = None) -> int:
        """FAILED jobs → PENDING for manual/automatic retry (attempts kept)."""
        if video_ids is None:
            cur = conn.execute(
                """
                UPDATE download_job
                SET status = 'PENDING', started_at = NULL, finished_at = NULL
                WHERE ustaz_id = ? AND status = 'FAILED'
                """,
                (ustaz_id,),
            )
            conn.execute(
                """
                UPDATE video
                SET download_status = 'PENDING',
                    state = CASE
                        WHEN state = 'FAILED' THEN 'QUEUED'
                        ELSE state
                    END
                WHERE ustaz_id = ? AND download_status = 'FAILED'
                """,
                (ustaz_id,),
            )
        else:
            n = 0
            for vid in video_ids:
                c = conn.execute(
                    """
                    UPDATE download_job
                    SET status = 'PENDING', started_at = NULL, finished_at = NULL
                    WHERE ustaz_id = ? AND video_id = ? AND status = 'FAILED'
                    """,
                    (ustaz_id, vid),
                )
                conn.execute(
                    """
                    UPDATE video
                    SET download_status = 'PENDING',
                        state = CASE WHEN state = 'FAILED' THEN 'QUEUED' ELSE state END
                    WHERE ustaz_id = ? AND video_id = ? AND download_status = 'FAILED'
                    """,
                    (ustaz_id, vid),
                )
                n += c.rowcount or 0
            conn.commit()
            return n
        conn.commit()
        return cur.rowcount or 0

    @staticmethod
    def enqueue_eligible(conn, ustaz_id: str, *, mode: str, max_attempts: int = 3) -> int:
        """Create download_job rows for videos that still need media (or meta mark)."""
        if mode == "metadata":
            # metadata-only: mark SKIPPED without media work
            cur = conn.execute(
                """
                UPDATE video
                SET download_status = 'SKIPPED',
                    state = CASE
                        WHEN state IN ('DISCOVERED', 'METADATA_READY', 'QUEUED', 'DOWNLOADING')
                        THEN 'DOWNLOADED'
                        ELSE state
                    END
                WHERE ustaz_id = ?
                  AND download_status IN ('PENDING', 'DOWNLOADING', 'FAILED')
                  AND state NOT IN ('DOWNLOADED', 'CLASSIFIED', 'GROUPED', 'INDEXED', 'PUBLISHED')
                """,
                (ustaz_id,),
            )
            conn.commit()
            return cur.rowcount or 0

        rows = conn.execute(
            """
            SELECT video_id FROM video
            WHERE ustaz_id = ?
              AND download_status = 'PENDING'
              AND state IN ('DISCOVERED', 'METADATA_READY', 'QUEUED', 'FAILED')
            """,
            (ustaz_id,),
        ).fetchall()
        enqueued = 0
        now = _utcnow()
        for row in rows:
            video_id = row["video_id"]
            conn.execute(
                """
                INSERT INTO download_job (ustaz_id, video_id, status, max_attempts,
                                          priority, created_at)
                VALUES (?, ?, 'PENDING', ?, 0, ?)
                ON CONFLICT (ustaz_id, video_id) DO UPDATE SET
                    status = CASE
                        WHEN download_job.status IN ('COMPLETED', 'SKIPPED')
                        THEN download_job.status
                        ELSE 'PENDING'
                    END,
                    error = NULL
                """,
                (ustaz_id, video_id, max_attempts, now),
            )
            conn.execute(
                """
                UPDATE video
                SET download_status = 'PENDING',
                    state = CASE
                        WHEN state IN ('DISCOVERED', 'METADATA_READY', 'FAILED')
                        THEN 'QUEUED'
                        ELSE state
                    END
                WHERE ustaz_id = ? AND video_id = ?
                """,
                (ustaz_id, video_id),
            )
            enqueued += 1
        # also re-queue existing FAILED jobs that are under max_attempts
        conn.execute(
            """
            UPDATE download_job SET status = 'PENDING'
            WHERE ustaz_id = ? AND status = 'FAILED' AND attempts < max_attempts
            """,
            (ustaz_id,),
        )
        conn.commit()
        return enqueued

    @staticmethod
    def claim_next(conn, ustaz_id: str):
        row = conn.execute(
            """
            SELECT j.id AS job_id, j.video_id, j.attempts, j.max_attempts
            FROM download_job j
            JOIN video v ON v.ustaz_id = j.ustaz_id AND v.video_id = j.video_id
            WHERE j.ustaz_id = ?
              AND j.status = 'PENDING'
              AND v.download_status != 'COMPLETED'
            ORDER BY j.priority DESC, j.id ASC
            LIMIT 1
            """,
            (ustaz_id,),
        ).fetchone()
        if row is None:
            return None
        now = _utcnow()
        conn.execute(
            "UPDATE download_job SET status = 'DOWNLOADING', started_at = ? WHERE id = ?",
            (now, row["job_id"]),
        )
        conn.execute(
            """
            UPDATE video
            SET download_status = 'DOWNLOADING', state = 'DOWNLOADING', last_seen_at = ?
            WHERE ustaz_id = ? AND video_id = ?
            """,
            (now, ustaz_id, row["video_id"]),
        )
        conn.commit()
        return dict(row)

    def mark_completed(
        self,
        conn,
        *,
        ustaz_id: str,
        video_id: str,
        job_id: int,
        path: Path | None,
        skipped: bool = False,
    ) -> None:
        now = _utcnow()
        status = "SKIPPED" if skipped else "COMPLETED"
        rel = None
        if path is not None:
            try:
                rel = str(path.resolve().relative_to(paths.REPO_ROOT.resolve()))
            except ValueError:
                rel = str(path)
        conn.execute(
            """
            UPDATE download_job
            SET status = ?, finished_at = ?, error = NULL
            WHERE id = ?
            """,
            (status, now, job_id),
        )
        conn.execute(
            """
            UPDATE video
            SET download_status = ?,
                state = 'DOWNLOADED',
                download_path = COALESCE(?, download_path),
                downloaded_at = ?,
                last_error = NULL
            WHERE ustaz_id = ? AND video_id = ?
            """,
            (status, rel, now, ustaz_id, video_id),
        )
        conn.execute(
            """
            INSERT INTO jobs (ustaz_id, video_id, job_type, status, created_at,
                              finished_at, error)
            VALUES (?, ?, 'DOWNLOAD', ?, ?, ?, NULL)
            """,
            (ustaz_id, video_id, status, now, now),
        )
        conn.commit()

    def mark_failed(
        self,
        conn,
        *,
        ustaz_id: str,
        video_id: str,
        job_id: int,
        error: str,
        attempts: int,
        max_attempts: int,
    ) -> None:
        now = _utcnow()
        exhausted = attempts >= max_attempts
        job_status = "FAILED" if exhausted else "PENDING"
        conn.execute(
            """
            UPDATE download_job
            SET status = ?, attempts = ?, error = ?, finished_at = ?
            WHERE id = ?
            """,
            (job_status, attempts, error, now if exhausted else None, job_id),
        )
        if exhausted:
            conn.execute(
                """
                UPDATE video
                SET download_status = 'FAILED',
                    state = 'FAILED',
                    error_count = error_count + 1,
                    last_error = ?
                WHERE ustaz_id = ? AND video_id = ?
                """,
                (error, ustaz_id, video_id),
            )
        else:
            conn.execute(
                """
                UPDATE video
                SET download_status = 'PENDING',
                    error_count = error_count + 1,
                    last_error = ?
                WHERE ustaz_id = ? AND video_id = ?
                """,
                (error, ustaz_id, video_id),
            )
        conn.execute(
            """
            INSERT INTO error_log (ustaz_id, video_id, job_id, stage, message, created_at)
            VALUES (?, ?, ?, 'download', ?, ?)
            """,
            (ustaz_id, video_id, job_id, error, now),
        )
        conn.execute(
            """
            INSERT INTO jobs (ustaz_id, video_id, job_type, status, created_at,
                              finished_at, error)
            VALUES (?, ?, 'DOWNLOAD', 'FAILED', ?, ?, ?)
            """,
            (ustaz_id, video_id, now, now if exhausted else None, error),
        )
        conn.commit()

    # ── validation ─────────────────────────────────────────────────

    @staticmethod
    def validate_file(path: Path | None, video_id: str) -> str | None:
        """Return error string or None if valid (spec §49)."""
        if path is None:
            return "no download path"
        if not path.is_file():
            return f"file missing: {path}"
        size = path.stat().st_size
        if size <= 0:
            return f"file empty: {path}"
        if video_id not in path.name:
            return f"video id not in filename: {path.name}"
        return None

    # ── orchestration ──────────────────────────────────────────────

    def run(
        self,
        ustaz_id: str,
        *,
        dry_run: bool = False,
        resume: bool = True,
        retry_failed: bool = False,
        max_jobs: int | None = None,
        db_path: Path | None = None,
        dest_dir: Path | None = None,
        rate_limit: RateLimitConfig | None = None,
    ) -> DownloadReport:
        try:
            merged = load_ustaz_config(ustaz_id)
        except ConfigError as exc:
            raise ConfigError(str(exc)) from exc

        universal = merged["universal"]
        ustaz_cfg = merged["ustaz"]
        mode = resolve_download_mode(universal, ustaz_cfg)
        rl = rate_limit or self.rate_limit or RateLimitConfig.from_universal(universal)
        max_attempts = int(universal.get("processing", {}).get("retry_count", 3))

        report = DownloadReport(ustaz_id=ustaz_id, dry_run=dry_run, mode=mode)
        dest = dest_dir or raw_videos_dir(ustaz_id)

        if not universal.get("youtube", {}).get("enabled", True) and mode != "metadata":
            report.errors.append("youtube disabled in universal config")
            return report

        conn = init_db(db_path)
        try:
            if retry_failed:
                report.resumed += self.reset_failed_for_retry(conn, ustaz_id)
            elif resume:
                report.resumed += self.reclaim_interrupted(conn, ustaz_id)

            if dry_run:
                pending = conn.execute(
                    """
                    SELECT count(*) AS c FROM video
                    WHERE ustaz_id = ? AND download_status = 'PENDING'
                      AND state IN ('DISCOVERED', 'METADATA_READY', 'QUEUED', 'FAILED')
                    """,
                    (ustaz_id,),
                ).fetchone()["c"]
                already = conn.execute(
                    "SELECT count(*) AS c FROM video WHERE ustaz_id = ? "
                    "AND download_status IN ('COMPLETED', 'SKIPPED')",
                    (ustaz_id,),
                ).fetchone()["c"]
                failed = conn.execute(
                    "SELECT count(*) AS c FROM video WHERE ustaz_id = ? "
                    "AND download_status = 'FAILED'",
                    (ustaz_id,),
                ).fetchone()["c"]
                report.enqueued = pending
                report.already_done = already
                report.failed = failed
                return report

            report.enqueued = self.enqueue_eligible(
                conn, ustaz_id, mode=mode, max_attempts=max_attempts
            )

            if mode == "metadata":
                report.skipped = report.enqueued
                report.already_done = conn.execute(
                    "SELECT count(*) AS c FROM video WHERE ustaz_id = ? "
                    "AND download_status IN ('COMPLETED', 'SKIPPED')",
                    (ustaz_id,),
                ).fetchone()["c"]
                return report

            processed = 0
            attempted: set[str] = set()
            while max_jobs is None or processed < max_jobs:
                job = self.claim_next(conn, ustaz_id)
                if job is None:
                    break
                if job["video_id"] in attempted:
                    # one attempt per video per run; leave job for next run
                    conn.execute(
                        "UPDATE download_job SET status = 'PENDING' WHERE id = ?",
                        (job["job_id"],),
                    )
                    conn.execute(
                        """
                        UPDATE video
                        SET download_status = 'PENDING',
                            state = CASE WHEN state = 'DOWNLOADING' THEN 'QUEUED' ELSE state END
                        WHERE ustaz_id = ? AND video_id = ?
                          AND download_status = 'DOWNLOADING'
                        """,
                        (ustaz_id, job["video_id"]),
                    )
                    conn.commit()
                    break
                attempted.add(job["video_id"])
                processed += 1

                video = conn.execute(
                    "SELECT * FROM video WHERE ustaz_id = ? AND video_id = ?",
                    (ustaz_id, job["video_id"]),
                ).fetchone()
                if video is None:
                    self.mark_failed(
                        conn,
                        ustaz_id=ustaz_id,
                        video_id=job["video_id"],
                        job_id=job["job_id"],
                        error="video row missing",
                        attempts=job["attempts"] + 1,
                        max_attempts=job["max_attempts"],
                    )
                    report.failed += 1
                    continue

                # skip if another path already completed
                if video["download_status"] in ("COMPLETED", "SKIPPED"):
                    self.mark_completed(
                        conn,
                        ustaz_id=ustaz_id,
                        video_id=job["video_id"],
                        job_id=job["job_id"],
                        path=None,
                        skipped=video["download_status"] == "SKIPPED",
                    )
                    report.already_done += 1
                    continue

                if processed > 1 and rl.source_spacing_seconds > 0:
                    delay = source_delay(rl, self._rng or __import__("random").random)
                    self._sleep(delay)

                request = DownloadRequest(
                    ustaz_id=ustaz_id,
                    video_id=job["video_id"],
                    web_url=video["web_url"]
                    or f"https://www.youtube.com/watch?v={job['video_id']}",
                    dest_dir=dest,
                    mode=mode,
                    upload_date=video["upload_date"],
                    title=video["title"],
                    spacing_seconds=rl.source_spacing_seconds,
                    sleep_requests=rl.sleep_between_requests,
                )

                try:
                    result = self.downloader.download(request)
                except Exception as exc:  # noqa: BLE001 - isolate per-job failures
                    result = DownloadResult(ok=False, error=str(exc))

                if not result.ok:
                    error = result.error or "unknown download error"
                    attempts = job["attempts"] + 1
                    self.mark_failed(
                        conn,
                        ustaz_id=ustaz_id,
                        video_id=job["video_id"],
                        job_id=job["job_id"],
                        error=error,
                        attempts=attempts,
                        max_attempts=job["max_attempts"],
                    )
                    report.failed += 1
                    report.errors.append(f"{job['video_id']}: {error}")
                    log_event(
                        "download",
                        error,
                        level=30,
                        ustaz_id=ustaz_id,
                        video_id=job["video_id"],
                        job_id=job["job_id"],
                    )
                    if is_throttle_signal(error) and rl.cooldown_seconds > 0:
                        self._sleep(rl.cooldown_seconds)
                    continue

                path = result.path
                if result.skipped:
                    self.mark_completed(
                        conn,
                        ustaz_id=ustaz_id,
                        video_id=job["video_id"],
                        job_id=job["job_id"],
                        path=None,
                        skipped=True,
                    )
                    report.skipped += 1
                    continue

                validation_error = self.validate_file(path, job["video_id"])
                if validation_error:
                    attempts = job["attempts"] + 1
                    self.mark_failed(
                        conn,
                        ustaz_id=ustaz_id,
                        video_id=job["video_id"],
                        job_id=job["job_id"],
                        error=f"validation: {validation_error}",
                        attempts=attempts,
                        max_attempts=job["max_attempts"],
                    )
                    report.failed += 1
                    report.errors.append(
                        f"{job['video_id']}: validation: {validation_error}"
                    )
                    continue

                self.mark_completed(
                    conn,
                    ustaz_id=ustaz_id,
                    video_id=job["video_id"],
                    job_id=job["job_id"],
                    path=path,
                    skipped=False,
                )
                report.completed += 1
                log_event(
                    "download",
                    "completed",
                    ustaz_id=ustaz_id,
                    video_id=job["video_id"],
                    job_id=job["job_id"],
                )

            report.already_done = conn.execute(
                "SELECT count(*) AS c FROM video WHERE ustaz_id = ? "
                "AND download_status IN ('COMPLETED', 'SKIPPED')",
                (ustaz_id,),
            ).fetchone()["c"]
            return report
        finally:
            conn.close()
