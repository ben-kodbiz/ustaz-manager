"""Conservative access pacing for YouTube (rate limits only — not stealth).

Allowed: fixed spacing, jittered delay, exponential backoff, cooldown after
throttling signals, yt-dlp's native --sleep-requests/--sleep-interval.

Forbidden by project spec (todoagent.md §26/§55/§62): CAPTCHA/anti-bot bypass,
fingerprint spoofing, stealth automation, proxy rotation to evade limits,
human-mimicry intended to defeat detection.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable


# Signals that YouTube (or yt-dlp) is throttling / demanding slower access.
# Cooldown and slower pacing only — never credential or fingerprint tricks.
_THROTTLE_MARKERS = (
    "http error 429",
    "too many requests",
    "rate-limit",
    "ratelimit",
    "rate limit",
    "quota exceeded",
    "please retry later",
    "confirm you're not a bot",
    "sign in to confirm",
    "not a bot",
)


@dataclass(frozen=True)
class RateLimitConfig:
    """Pacing knobs from config/universal.json → youtube.rate_limit."""

    sleep_between_requests: float = 2.0
    max_sleep_between_requests: float = 4.0
    source_spacing_seconds: float = 3.0
    source_spacing_jitter_seconds: float = 1.0
    cooldown_seconds: float = 120.0
    max_consecutive_errors: int = 3

    @classmethod
    def from_universal(cls, universal: dict[str, Any]) -> "RateLimitConfig":
        raw = (universal.get("youtube") or {}).get("rate_limit") or {}
        base = cls()
        return cls(
            sleep_between_requests=float(
                raw.get("sleep_between_requests", base.sleep_between_requests)
            ),
            max_sleep_between_requests=float(
                raw.get(
                    "max_sleep_between_requests", base.max_sleep_between_requests
                )
            ),
            source_spacing_seconds=float(
                raw.get("source_spacing_seconds", base.source_spacing_seconds)
            ),
            source_spacing_jitter_seconds=float(
                raw.get(
                    "source_spacing_jitter_seconds",
                    base.source_spacing_jitter_seconds,
                )
            ),
            cooldown_seconds=float(
                raw.get("cooldown_seconds", base.cooldown_seconds)
            ),
            max_consecutive_errors=int(
                raw.get("max_consecutive_errors", base.max_consecutive_errors)
            ),
        )


def is_throttle_signal(message: str) -> bool:
    text = (message or "").lower()
    return any(marker in text for marker in _THROTTLE_MARKERS)


def source_delay(cfg: RateLimitConfig, rng: Callable[[], float] = random.random) -> float:
    """Fixed inter-source spacing plus bounded jitter (not human-mimicry)."""
    if cfg.source_spacing_seconds <= 0:
        return 0.0
    jitter = cfg.source_spacing_jitter_seconds * max(0.0, rng())
    return cfg.source_spacing_seconds + jitter


def backoff_delay(
    cfg: RateLimitConfig,
    attempt: int,
    *,
    base: float | None = None,
    rng: Callable[[], float] = random.random,
    throttled: bool = False,
) -> float:
    """Exponential backoff; throttle signals use at least cooldown_seconds."""
    base_delay = cfg.cooldown_seconds if base is None else base
    if base_delay <= 0:
        base_delay = 30.0
    delay = base_delay * (2**max(0, attempt))
    if throttled:
        delay = max(delay, cfg.cooldown_seconds)
    delay += cfg.source_spacing_jitter_seconds * max(0.0, rng())
    return delay


class PaceGuard:
    """Tracks consecutive failures and enforces cooldown between source fetches."""

    def __init__(
        self,
        cfg: RateLimitConfig,
        *,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ):
        self.cfg = cfg
        self._sleep = sleep
        self._rng = rng
        self.consecutive_errors = 0
        self._last_source_at: float | None = None

    def pace_sources(self) -> None:
        delay = source_delay(self.cfg, self._rng)
        if delay > 0:
            self._sleep(delay)

    def note_success(self) -> None:
        self.consecutive_errors = 0

    def note_failure(self, message: str) -> float:
        """Record a failure; return (and apply) any forced cooldown sleep."""
        self.consecutive_errors += 1
        if is_throttle_signal(message):
            self._sleep(self.cfg.cooldown_seconds)
            self.consecutive_errors = 0
            return self.cfg.cooldown_seconds
        if self.consecutive_errors >= self.cfg.max_consecutive_errors:
            self._sleep(self.cfg.cooldown_seconds)
            self.consecutive_errors = 0
            return self.cfg.cooldown_seconds
        return 0.0

    def abort_for_sustained_errors(self) -> bool:
        return self.consecutive_errors >= self.cfg.max_consecutive_errors
