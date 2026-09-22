"""YouTube source adapter via the yt-dlp CLI (metadata-only, no download).

Uses yt-dlp's native sleep options for polite request pacing. Does not
spoof fingerprints, rotate identities, or otherwise evade anti-bot controls.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from core.rate_limit import RateLimitConfig
from core.sources.base import SourceAdapter, SourceListing

# Injected for tests: (url, kind, limit, pacing) -> SourceListing
Extractor = Callable[..., SourceListing]


class YouTubeError(Exception):
    pass


@dataclass(frozen=True)
class YtDlpPacing:
    """Arguments passed to yt-dlp for conservative pacing."""

    sleep_requests: float = 2.0
    max_sleep_interval: float = 4.0


def find_ytdlp() -> str:
    path = shutil.which("yt-dlp")
    if not path:
        raise YouTubeError(
            "yt-dlp not found on PATH; install yt-dlp to run discovery (Phase 2)"
        )
    return path


def pacing_from_rate_limit(cfg: RateLimitConfig) -> YtDlpPacing:
    sleep_requests = max(0.0, cfg.sleep_between_requests)
    max_sleep = max(sleep_requests, cfg.max_sleep_between_requests)
    return YtDlpPacing(sleep_requests=sleep_requests, max_sleep_interval=max_sleep)


def _entry_video_id(entry: dict[str, Any]) -> str | None:
    if entry.get("id"):
        return str(entry["id"])
    url = entry.get("url") or entry.get("webpage_url") or ""
    if "v=" in url:
        return url.split("v=", 1)[1].split("&", 1)[0] or None
    if "/" in url and len(url.rsplit("/", 1)[-1]) == 11:
        return url.rsplit("/", 1)[-1]
    return None


def normalize_flat_entry(
    entry: dict[str, Any],
    *,
    parent: dict[str, Any] | None = None,
    position: int | None = None,
) -> dict[str, Any] | None:
    """Map one yt-dlp flat entry to our raw-ish video dict; None if unusable."""
    video_id = _entry_video_id(entry)
    if not video_id:
        return None

    parent = parent or {}
    playlist_id = entry.get("playlist_id") or parent.get("id")
    playlist_title = entry.get("playlist_title") or (
        parent.get("title") if parent.get("_type") in ("playlist", "multi_video") else None
    )
    if entry.get("playlist_index") is not None:
        playlist_position = entry.get("playlist_index")
    elif position is not None:
        playlist_position = position
    else:
        playlist_position = entry.get("playlist_position")

    channel_id = entry.get("channel_id") or entry.get("uploader_id") or parent.get("channel_id")
    channel_name = (
        entry.get("channel")
        or entry.get("uploader")
        or parent.get("channel")
        or parent.get("uploader")
    )

    duration = entry.get("duration")
    if duration is not None:
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            duration = None

    upload_date = entry.get("upload_date")
    if upload_date and len(str(upload_date)) == 8:
        raw_date = str(upload_date)
        upload_date = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
    else:
        upload_date = None

    thumbnail = entry.get("thumbnail")
    if not thumbnail and entry.get("thumbnails"):
        thumbs = entry["thumbnails"]
        thumbnail = thumbs[-1].get("url") if thumbs else None

    web_url = entry.get("webpage_url")
    if not web_url and video_id:
        web_url = f"https://www.youtube.com/watch?v={video_id}"

    return {
        "video_id": video_id,
        "title": entry.get("title"),
        "description": entry.get("description"),
        "channel_id": channel_id,
        "channel_name": channel_name,
        "playlist_id": playlist_id if playlist_id and str(playlist_id).startswith(("PL", "UU", "OL", "FL", "RD")) else (
            playlist_id if parent.get("_type") == "playlist" else playlist_id
        ),
        "playlist_title": playlist_title,
        "playlist_position": int(playlist_position) if playlist_position is not None else None,
        "upload_date": upload_date,
        "duration": duration,
        "thumbnail_url": thumbnail,
        "web_url": web_url,
        "view_count": entry.get("view_count"),
    }


def build_ytdlp_cmd(
    url: str,
    *,
    kind: str,
    limit: int | None = None,
    pacing: YtDlpPacing | None = None,
) -> list[str]:
    """Argv-only yt-dlp command. Never a shell string; no spoofed headers."""
    binary = find_ytdlp()
    pacing = pacing or YtDlpPacing()
    cmd: list[str] = [
        binary,
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--ignore-errors",
        "--no-call-home",
        "--socket-timeout",
        "30",
        # Native polite pacing (requests + optional interval between items)
        "--sleep-requests",
        f"{pacing.sleep_requests:.3f}",
        "--sleep-interval",
        "0",
        "--max-sleep-interval",
        f"{pacing.max_sleep_interval:.3f}",
    ]
    if limit is not None and limit > 0:
        cmd.extend(["--playlist-end", str(limit)])
    # Prefer the channel videos tab for handle/channel URLs
    if kind == "youtube_channel" and not url.rstrip("/").endswith(
        ("/videos", "/streams", "/shorts")
    ):
        if (
            url.rstrip("/").endswith("@")
            or "@" in url
            or "/channel/" in url
            or url.rstrip("/").endswith(".com")
        ):
            url = url.rstrip("/") + "/videos"
    cmd.append(url)
    return cmd


def run_ytdlp_json(
    url: str,
    *,
    kind: str,
    limit: int | None = None,
    timeout: int = 120,
    pacing: YtDlpPacing | None = None,
) -> dict[str, Any]:
    """Run yt-dlp flat extraction and parse JSON. Never builds a shell string."""
    cmd = build_ytdlp_cmd(url, kind=kind, limit=limit, pacing=pacing)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise YouTubeError(f"yt-dlp timed out for {url}") from exc
    except OSError as exc:
        raise YouTubeError(f"failed to run yt-dlp: {exc}") from exc

    stdout = (proc.stdout or "").strip()
    if not stdout:
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit {proc.returncode}"
        raise YouTubeError(f"yt-dlp produced no JSON for {url}: {tail}")

    # With --ignore-errors, partial documents may still parse; take last line if needed
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        for line in reversed(stdout.splitlines()):
            line = line.strip().rstrip(",")
            if line.startswith("{") or line.startswith("["):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        raise YouTubeError(f"yt-dlp returned invalid JSON for {url}")


class YouTubeAdapter(SourceAdapter):
    name = "youtube"

    def __init__(
        self,
        extractor: Extractor | None = None,
        *,
        rate_limit: RateLimitConfig | None = None,
    ):
        self.rate_limit = rate_limit or RateLimitConfig()
        self._extractor = extractor or self._default_extract

    def _default_extract(
        self, url: str, kind: str, limit: int | None, *args: Any, **kwargs: Any
    ) -> SourceListing:
        try:
            data = run_ytdlp_json(
                url,
                kind=kind,
                limit=limit,
                pacing=pacing_from_rate_limit(self.rate_limit),
            )
        except YouTubeError as exc:
            return SourceListing(url=url, kind=kind, error=str(exc))

        entries_raw = data.get("entries")
        if entries_raw is None and data.get("_type") not in ("playlist", "multi_video"):
            # single video result — wrap it
            video = normalize_flat_entry(data, parent=data)
            entries_raw = [video] if video else []
            source_id = data.get("id")
            source_title = data.get("title")
        else:
            entries_raw = entries_raw or []
            source_id = data.get("id")
            source_title = data.get("title")

        entries: list[dict[str, Any]] = []
        for i, raw in enumerate(entries_raw):
            if raw is None:
                continue
            if isinstance(raw, dict) and raw.get("video_id") and "web_url" in raw:
                # already normalized (single-video path)
                entries.append(raw)
                continue
            if not isinstance(raw, dict):
                continue
            # playlist_position sometimes only present on flat entries
            if raw.get("playlist_index") is None and i is not None:
                flat = dict(raw)
                flat.setdefault("playlist_index", i + 1)
            else:
                flat = raw
            normalized = normalize_flat_entry(flat, parent=data if data.get("_type") == "playlist" else None, position=i + 1)
            if normalized:
                if kind == "youtube_channel":
                    normalized["playlist_id"] = None
                    normalized["playlist_title"] = None
                    normalized["playlist_position"] = None
                entries.append(normalized)

        return SourceListing(
            url=url,
            kind=kind,
            entries=entries,
            source_id=str(source_id) if source_id is not None else None,
            source_title=source_title,
        )

    def list_source(self, url: str, *, kind: str, limit: int | None = None) -> SourceListing:
        return self._extractor(url, kind, limit)
