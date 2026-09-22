"""Downloader plugin interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DownloadRequest:
    ustaz_id: str
    video_id: str
    web_url: str
    dest_dir: Path
    mode: str  # metadata | audio | video
    upload_date: str | None = None
    title: str | None = None
    spacing_seconds: float = 2.0
    sleep_requests: float = 2.0


@dataclass
class DownloadResult:
    ok: bool
    path: Path | None = None
    error: str | None = None
    skipped: bool = False

    @classmethod
    def skip(cls) -> "DownloadResult":
        return cls(ok=True, path=None, error=None, skipped=True)


class Downloader(ABC):
    name: str = "base"

    @abstractmethod
    def download(self, request: DownloadRequest) -> DownloadResult:
        """Download one video (or skip). Never raise for expected failures."""
