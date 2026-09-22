"""Discovery stage: list sources → persist video/playlist metadata in SQLite.

Discovery never downloads media and never marks videos as downloaded.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core import paths
from core.config import ConfigError, load_ustaz_config
from core.database import init_db
from core.logging_setup import log_event
from core.rate_limit import PaceGuard, RateLimitConfig, backoff_delay, is_throttle_signal, source_delay
from core.sources.base import SourceAdapter, SourceListing
from core.sources.youtube import YouTubeAdapter


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def metadata_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def matches_speakers(entry: dict[str, Any], speaker_names: list[str] | None) -> bool:
    """Speaker attribution for multi-speaker publisher channels.

    True when no filter is configured, otherwise True only when one of the
    configured names appears (case-insensitive substring) in the video title
    or description. channel_name is the publisher, never the speaker.
    """
    names = [str(n).strip().lower() for n in (speaker_names or []) if str(n).strip()]
    if not names:
        return True
    haystack = f"{entry.get('title') or ''}\n{entry.get('description') or ''}".lower()
    return any(name in haystack for name in names)


@dataclass
class DiscoveryReport:
    ustaz_id: str
    dry_run: bool = False
    sources_total: int = 0
    sources_ok: int = 0
    sources_failed: int = 0
    videos_new: int = 0
    videos_updated: int = 0
    videos_unchanged: int = 0
    videos_skipped: int = 0
    videos_pruned: int = 0
    playlists_touched: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def videos_seen(self) -> int:
        return self.videos_new + self.videos_updated + self.videos_unchanged


class DiscoveryService:
    def __init__(
        self,
        adapter: SourceAdapter | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
        raw_dir: Path | None = None,
    ):
        self.adapter = adapter or YouTubeAdapter()
        self._sleep = sleep
        self._rng = rng
        self._raw_dir = raw_dir or paths.RAW_DIR

    # ── config-driven source list ──────────────────────────────────

    @staticmethod
    def sources_from_config(ustaz_cfg: dict) -> list[tuple[str, str]]:
        sources = ustaz_cfg.get("sources") or {}
        out: list[tuple[str, str]] = []
        for url in sources.get("youtube_channels") or []:
            out.append((url, "youtube_channel"))
        for url in sources.get("youtube_playlists") or []:
            out.append((url, "youtube_playlist"))
        return out

    # ── persistence ────────────────────────────────────────────────

    def _write_raw(self, video_id: str, raw: dict[str, Any], *, dry_run: bool) -> Path | None:
        if dry_run:
            return None
        dest = self._raw_dir / "youtube"
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"{video_id}.json"
        path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _upsert_playlist(
        conn,
        *,
        ustaz_id: str,
        playlist_id: str | None,
        playlist_title: str | None,
        dry_run: bool,
    ) -> bool:
        if not playlist_id or dry_run:
            return False
        now = _utcnow()
        row = conn.execute(
            "SELECT playlist_id FROM playlist WHERE ustaz_id = ? AND playlist_id = ?",
            (ustaz_id, playlist_id),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO playlist (ustaz_id, playlist_id, title, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (ustaz_id, playlist_id, playlist_title, now, now),
            )
            return True
        conn.execute(
            "UPDATE playlist SET title = COALESCE(?, title), last_seen_at = ? "
            "WHERE ustaz_id = ? AND playlist_id = ?",
            (playlist_title, now, ustaz_id, playlist_id),
        )
        return True

    def upsert_video(
        self,
        conn,
        *,
        ustaz_id: str,
        entry: dict[str, Any],
        dry_run: bool = False,
    ) -> str:
        """Insert or update one video. Returns 'new' | 'updated' | 'unchanged'."""
        video_id = entry["video_id"]
        now = _utcnow()
        normalized = {
            "video_id": video_id,
            "title": entry.get("title"),
            "description": entry.get("description"),
            "channel_id": entry.get("channel_id"),
            "channel_name": entry.get("channel_name"),
            "playlist_id": entry.get("playlist_id"),
            "playlist_title": entry.get("playlist_title"),
            "playlist_position": entry.get("playlist_position"),
            "upload_date": entry.get("upload_date"),
            "duration": entry.get("duration"),
            "thumbnail_url": entry.get("thumbnail_url"),
            "web_url": entry.get("web_url"),
        }
        new_hash = metadata_hash(normalized)

        existing = conn.execute(
            "SELECT * FROM video WHERE ustaz_id = ? AND video_id = ?",
            (ustaz_id, video_id),
        ).fetchone()

        if existing is None:
            if not dry_run:
                conn.execute(
                    """
                    INSERT INTO video (
                        ustaz_id, video_id, title, original_title, description,
                        channel_id, channel_name, playlist_id, playlist_title,
                        playlist_position, upload_date, duration, thumbnail_url,
                        web_url, state, download_status, classification_status,
                        first_seen_at, last_seen_at, metadata_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              'METADATA_READY', 'PENDING', 'PENDING',
                              ?, ?, ?)
                    """,
                    (
                        ustaz_id,
                        video_id,
                        normalized["title"],
                        normalized["title"],
                        normalized["description"],
                        normalized["channel_id"],
                        normalized["channel_name"],
                        normalized["playlist_id"],
                        normalized["playlist_title"],
                        normalized["playlist_position"],
                        normalized["upload_date"],
                        normalized["duration"],
                        normalized["thumbnail_url"],
                        normalized["web_url"],
                        now,
                        now,
                        new_hash,
                    ),
                )
                self._write_raw(video_id, {"entry": entry, "hash": new_hash}, dry_run=dry_run)
                self._upsert_playlist(
                    conn,
                    ustaz_id=ustaz_id,
                    playlist_id=normalized["playlist_id"],
                    playlist_title=normalized["playlist_title"],
                    dry_run=dry_run,
                )
            return "new"

        # Known video: update metadata if changed; never reset download state
        changed_hash = existing["metadata_hash"] != new_hash
        changed_playlist = (
            normalized["playlist_id"] is not None
            and (
                existing["playlist_id"] != normalized["playlist_id"]
                or existing["playlist_position"] != normalized["playlist_position"]
            )
        )
        if not changed_hash and not changed_playlist and not dry_run:
            conn.execute(
                "UPDATE video SET last_seen_at = ? WHERE ustaz_id = ? AND video_id = ?",
                (now, ustaz_id, video_id),
            )
            self._write_raw(video_id, {"entry": entry, "hash": new_hash}, dry_run=False)
            return "unchanged"
        if dry_run:
            return "updated" if (changed_hash or changed_playlist) else "unchanged"

        # note: download_status / download_path intentionally untouched (Phase 3 owns them)

        conn.execute(
            """
            UPDATE video SET
                title = ?,
                description = COALESCE(?, description),
                channel_id = COALESCE(?, channel_id),
                channel_name = COALESCE(?, channel_name),
                playlist_id = COALESCE(?, playlist_id),
                playlist_title = COALESCE(?, playlist_title),
                playlist_position = COALESCE(?, playlist_position),
                upload_date = COALESCE(?, upload_date),
                duration = COALESCE(?, duration),
                thumbnail_url = COALESCE(?, thumbnail_url),
                web_url = COALESCE(?, web_url),
                state = CASE
                    WHEN state = 'DISCOVERED' THEN 'METADATA_READY'
                    ELSE state
                END,
                last_seen_at = ?,
                metadata_hash = ?
            WHERE ustaz_id = ? AND video_id = ?
            """,
            (
                normalized["title"],
                normalized["description"],
                normalized["channel_id"],
                normalized["channel_name"],
                normalized["playlist_id"],
                normalized["playlist_title"],
                normalized["playlist_position"],
                normalized["upload_date"],
                normalized["duration"],
                normalized["thumbnail_url"],
                normalized["web_url"],
                now,
                new_hash,
                ustaz_id,
                video_id,
            ),
        )
        self._write_raw(video_id, {"entry": entry, "hash": new_hash}, dry_run=dry_run)
        self._upsert_playlist(
            conn,
            ustaz_id=ustaz_id,
            playlist_id=normalized["playlist_id"],
            playlist_title=normalized["playlist_title"],
            dry_run=dry_run,
        )
        return "updated"

    def _persist_source(
        self,
        conn,
        *,
        ustaz_id: str,
        listing: SourceListing,
        dry_run: bool,
    ) -> None:
        if dry_run or listing.error:
            return
        now = _utcnow()
        conn.execute(
            """
            INSERT INTO source (ustaz_id, source_type, url, external_id, title,
                                discovered_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (ustaz_id, source_type, url) DO UPDATE SET
                external_id = COALESCE(excluded.external_id, external_id),
                title = COALESCE(excluded.title, title),
                last_seen_at = excluded.last_seen_at
            """,
            (
                ustaz_id,
                listing.kind,
                listing.url,
                listing.source_id,
                listing.source_title,
                now,
                now,
            ),
        )

    def _prune_non_matching(
        self,
        conn,
        *,
        ustaz_id: str,
        speaker_names: list,
        dry_run: bool,
    ) -> int:
        """Delete stored videos that fail the speaker filter. Explicit only.

        Refuses to act when no speaker filter is configured (that would wipe
        the archive).         Raw metadata files under data/raw/ are shared by
        video_id across Ustaz and are left untouched; only DB rows for this
        (ustaz_id, video_id) are removed.
        """
        if not [str(n).strip() for n in (speaker_names or []) if str(n).strip()]:
            return -1  # signal: refused; caller records the error
        rows = conn.execute(
            "SELECT video_id, title, description FROM video WHERE ustaz_id = ?",
            (ustaz_id,),
        ).fetchall()
        doomed = [
            str(r["video_id"])
            for r in rows
            if not matches_speakers(dict(r), speaker_names)
        ]
        if dry_run or not doomed:
            return len(doomed)
        for video_id in doomed:
            for table in ("video_series", "video_category", "download_job", "video"):
                conn.execute(
                    f"DELETE FROM {table} WHERE ustaz_id = ? AND video_id = ?",
                    (ustaz_id, video_id),
                )
        log_event(
            "discovery",
            f"pruned {len(doomed)} non-matching videos",
            ustaz_id=ustaz_id,
        )
        return len(doomed)

    # ── orchestration ──────────────────────────────────────────────

    def discover(
        self,
        ustaz_id: str,
        *,
        dry_run: bool = False,
        limit: int | None = None,
        spacing_seconds: float | None = None,
        retry_count: int | None = None,
        backoff_seconds: float | None = None,
        db_path: Path | None = None,
        raw_dir: Path | None = None,
        rate_limit: RateLimitConfig | None = None,
        prune: bool = False,
    ) -> DiscoveryReport:
        try:
            merged = load_ustaz_config(ustaz_id)
        except ConfigError as exc:
            raise ConfigError(str(exc)) from exc

        ustaz_cfg = merged["ustaz"]
        universal = merged["universal"]

        if not universal.get("youtube", {}).get("enabled", True):
            report = DiscoveryReport(ustaz_id=ustaz_id, dry_run=dry_run)
            report.errors.append("youtube.enabled is false in universal config")
            return report

        rl = rate_limit or RateLimitConfig.from_universal(universal)
        # CLI overrides (None = use config defaults)
        if spacing_seconds is None:
            spacing_seconds = rl.source_spacing_seconds
        if retry_count is None:
            retry_count = int(universal.get("processing", {}).get("retry_count", 3))
        if backoff_seconds is None:
            backoff_seconds = float(
                universal.get("processing", {}).get("backoff_seconds", 30)
            )

        # Ensure the adapter itself paces yt-dlp HTTP requests natively
        if isinstance(self.adapter, YouTubeAdapter):
            self.adapter.rate_limit = rl

        guard = PaceGuard(rl, sleep=self._sleep, rng=self._rng)

        sources = self.sources_from_config(ustaz_cfg)
        speaker_names = (ustaz_cfg.get("content") or {}).get("speaker_names") or []
        report = DiscoveryReport(ustaz_id=ustaz_id, dry_run=dry_run, sources_total=len(sources))
        if not sources:
            report.errors.append("no youtube sources configured")
            return report

        conn = init_db(db_path)
        if raw_dir is not None:
            self._raw_dir = raw_dir
        try:
            for index, (url, kind) in enumerate(sources):
                if index > 0 and spacing_seconds and spacing_seconds > 0:
                    if spacing_seconds != rl.source_spacing_seconds:
                        delay = spacing_seconds + self._rng() * max(
                            0.0, rl.source_spacing_jitter_seconds
                        )
                    else:
                        delay = source_delay(rl, self._rng)
                    if delay > 0:
                        self._sleep(delay)

                listing = self._fetch_with_retry(
                    url,
                    kind,
                    limit=limit,
                    retry_count=retry_count,
                    backoff_seconds=backoff_seconds,
                    report=report,
                    rate_limit=rl,
                    guard=guard,
                )
                if listing is None or listing.error:
                    report.sources_failed += 1
                    if listing and listing.error:
                        report.errors.append(f"{url}: {listing.error}")
                        log_event(
                            "discovery",
                            listing.error,
                            level=30,
                            ustaz_id=ustaz_id,
                        )
                        if is_throttle_signal(listing.error):
                            log_event(
                                "discovery",
                                "throttle signal — applying cooldown",
                                level=30,
                                ustaz_id=ustaz_id,
                            )
                    continue

                report.sources_ok += 1
                guard.note_success()
                self._persist_source(
                    conn, ustaz_id=ustaz_id, listing=listing, dry_run=dry_run
                )

                for entry in listing.entries:
                    if not matches_speakers(entry, speaker_names):
                        report.videos_skipped += 1
                        continue
                    try:
                        outcome = self.upsert_video(
                            conn, ustaz_id=ustaz_id, entry=entry, dry_run=dry_run
                        )
                    except Exception as exc:  # noqa: BLE001 - isolate per-video failures
                        report.errors.append(
                            f"{entry.get('video_id')}: {exc}"
                        )
                        continue
                    if outcome == "new":
                        report.videos_new += 1
                    elif outcome == "updated":
                        report.videos_updated += 1
                    else:
                        report.videos_unchanged += 1

                if listing.kind == "youtube_playlist":
                    report.playlists_touched += 1

                log_event(
                    "discovery",
                    f"{kind} {url}: {len(listing.entries)} entries",
                    ustaz_id=ustaz_id,
                )

            if prune:
                pruned = self._prune_non_matching(
                    conn,
                    ustaz_id=ustaz_id,
                    speaker_names=speaker_names,
                    dry_run=dry_run,
                )
                if pruned < 0:
                    report.errors.append(
                        "prune refused: content.speaker_names is not set"
                    )
                else:
                    report.videos_pruned = pruned

            if dry_run:
                conn.rollback()
            else:
                conn.commit()
        finally:
            conn.close()

        return report

    def _fetch_with_retry(
        self,
        url: str,
        kind: str,
        *,
        limit: int | None,
        retry_count: int,
        backoff_seconds: float,
        report: DiscoveryReport,
        rate_limit: RateLimitConfig | None = None,
        guard: PaceGuard | None = None,
    ) -> SourceListing | None:
        rl = rate_limit or RateLimitConfig()
        attempts = max(1, retry_count)
        last: SourceListing | None = None
        for attempt in range(attempts):
            try:
                last = self.adapter.list_source(url, kind=kind, limit=limit)
            except Exception as exc:  # noqa: BLE001 - adapter errors become listing errors
                last = SourceListing(url=url, kind=kind, error=str(exc))
            if last is None:
                last = SourceListing(url=url, kind=kind, error="empty adapter result")
            if not last.error:
                if guard:
                    guard.note_success()
                return last

            throttled = is_throttle_signal(last.error or "")
            if guard:
                cooldown = guard.note_failure(last.error or "")
                if cooldown > 0 and attempt >= attempts - 1:
                    # note_failure already slept; surface for logs
                    report.errors.append(
                        f"{url}: cooldown {int(cooldown)}s after throttle/error"
                    )

            if attempt < attempts - 1:
                delay = backoff_delay(
                    rl,
                    attempt,
                    base=backoff_seconds,
                    rng=self._rng,
                    throttled=throttled,
                )
                log_event(
                    "discovery",
                    f"retry {attempt + 1}/{attempts - 1} for {url} in {delay:.1f}s",
                    level=30,
                    ustaz_id=report.ustaz_id,
                )
                self._sleep(delay)
        return last
