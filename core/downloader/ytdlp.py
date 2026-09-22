"""yt-dlp downloader (single file, conservative pacing, no evasion)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from core.downloader.base import DownloadRequest, DownloadResult, Downloader

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def slugify_title(title: str | None, max_len: int = 60) -> str:
    text = _SAFE.sub("-", (title or "untitled").strip()).strip("-").lower()
    return (text or "untitled")[:max_len].strip("-") or "untitled"


def output_template(request: DownloadRequest) -> str:
    """Deterministic name: <upload-date>_<video-id>_<slug>.<ext> (spec §28)."""
    date = (request.upload_date or "0000-00-00").replace("-", "")
    slug = slugify_title(request.title)
    # yt-dlp template — video_id is the immutable identity in the filename
    return f"{date}_{request.video_id}_{slug}.%(ext)s"


def find_ytdlp() -> str | None:
    return shutil.which("yt-dlp")


def build_ytdlp_cmd(request: DownloadRequest, binary: str) -> list[str]:
    template = output_template(request)
    out = str(request.dest_dir / template)
    cmd: list[str] = [
        binary,
        "--no-playlist",
        "--continue",
        "--no-overwrites",
        "--no-warnings",
        "--socket-timeout",
        "30",
        "--sleep-requests",
        f"{max(0.0, request.sleep_requests):.3f}",
        "-o",
        out,
    ]
    if request.mode == "audio":
        cmd.extend(["-x", "--audio-format", "m4a"])
    elif request.mode == "video":
        cmd.extend(["-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b"])
    # mode == metadata handled by caller (never reaches binary)
    cmd.append(request.web_url)
    return cmd


class YtDlpDownloader(Downloader):
    name = "ytdlp"

    def download(self, request: DownloadRequest) -> DownloadResult:
        if request.mode == "metadata":
            return DownloadResult(ok=True, skipped=True)

        binary = find_ytdlp()
        if not binary:
            return DownloadResult(
                ok=False, error="yt-dlp not found on PATH"
            )

        request.dest_dir.mkdir(parents=True, exist_ok=True)
        cmd = build_ytdlp_cmd(request, binary)
        # pacing between downloads is handled by DownloadManager; here we
        # only pass yt-dlp-native --sleep-requests (no spoofed headers).
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return DownloadResult(ok=False, error=f"yt-dlp timed out for {request.video_id}")
        except OSError as exc:
            return DownloadResult(ok=False, error=f"failed to run yt-dlp: {exc}")

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            tail = stderr.splitlines()[-1] if stderr else f"exit {proc.returncode}"
            return DownloadResult(ok=False, error=tail)

        found = _find_downloaded_file(request)
        if found is None:
            return DownloadResult(
                ok=False,
                error=f"yt-dlp succeeded but file missing for {request.video_id}",
            )
        return DownloadResult(ok=True, path=found)


def _find_downloaded_file(request: DownloadRequest) -> Path | None:
    """Locate file containing the video_id under dest_dir (identity check)."""
    if not request.dest_dir.is_dir():
        return None
    pattern = f"*_{request.video_id}_*"
    candidates = sorted(request.dest_dir.glob(pattern))
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path
    # also accept partial yt-dlp fragments naming if final rename pending
    return None
