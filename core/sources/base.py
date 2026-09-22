"""Source adapter interface. One implementation per platform."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceListing:
    """Result of listing entries from one source URL (metadata only)."""

    url: str
    kind: str  # youtube_channel | youtube_playlist | ...
    entries: list[dict[str, Any]] = field(default_factory=list)
    source_id: str | None = None
    source_title: str | None = None
    error: str | None = None


class SourceAdapter(ABC):
    """Discover video metadata without downloading media."""

    name: str = "base"

    @abstractmethod
    def list_source(self, url: str, *, kind: str, limit: int | None = None) -> SourceListing:
        """Return flat metadata entries for one channel/playlist URL."""
