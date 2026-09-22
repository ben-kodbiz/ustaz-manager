"""Tests for conservative rate limiting (pacing only — not stealth)."""

from __future__ import annotations

import unittest

from core.rate_limit import (
    PaceGuard,
    RateLimitConfig,
    backoff_delay,
    is_throttle_signal,
    source_delay,
)
from core.sources.youtube import YtDlpPacing, build_ytdlp_cmd, pacing_from_rate_limit


class TestThrottleDetection(unittest.TestCase):
    def test_throttle_markers(self) -> None:
        for msg in (
            "HTTP Error 429: Too Many Requests",
            "ERROR: unable to download webpage: ... rate limit ...",
            "Sign in to confirm you're not a bot",
            "Please retry later",
        ):
            self.assertTrue(is_throttle_signal(msg), msg)

    def test_normal_errors_not_throttle(self) -> None:
        self.assertFalse(is_throttle_signal("Video unavailable"))
        self.assertFalse(is_throttle_signal("network unreachable"))


class TestDelays(unittest.TestCase):
    def test_source_delay_includes_jitter_bound(self) -> None:
        cfg = RateLimitConfig(source_spacing_seconds=3.0, source_spacing_jitter_seconds=1.0)
        self.assertEqual(source_delay(cfg, rng=lambda: 0.0), 3.0)
        self.assertEqual(source_delay(cfg, rng=lambda: 1.0), 4.0)

    def test_backoff_exponential(self) -> None:
        cfg = RateLimitConfig(cooldown_seconds=60.0, source_spacing_jitter_seconds=0.0)
        d0 = backoff_delay(cfg, 0, base=30.0, rng=lambda: 0.0)
        d1 = backoff_delay(cfg, 1, base=30.0, rng=lambda: 0.0)
        self.assertEqual(d0, 30.0)
        self.assertEqual(d1, 60.0)

    def test_throttled_backoff_at_least_cooldown(self) -> None:
        cfg = RateLimitConfig(cooldown_seconds=120.0, source_spacing_jitter_seconds=0.0)
        delay = backoff_delay(cfg, 0, base=30.0, rng=lambda: 0.0, throttled=True)
        self.assertGreaterEqual(delay, 120.0)


class TestPaceGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.sleeps: list[float] = []
        self.cfg = RateLimitConfig(
            source_spacing_seconds=2.0,
            source_spacing_jitter_seconds=0.5,
            cooldown_seconds=90.0,
            max_consecutive_errors=3,
        )
        self.guard = PaceGuard(
            self.cfg, sleep=self.sleeps.append, rng=lambda: 0.0
        )

    def test_pace_sources_sleeps(self) -> None:
        self.guard.pace_sources()
        self.assertEqual(self.sleeps, [2.0])

    def test_success_resets_consecutive(self) -> None:
        self.guard.note_failure("temporary")
        self.guard.note_failure("temporary")
        self.guard.note_success()
        self.assertEqual(self.guard.consecutive_errors, 0)

    def test_consecutive_errors_trigger_cooldown(self) -> None:
        self.guard.note_failure("a")
        self.guard.note_failure("b")
        self.assertEqual(self.sleeps, [])
        self.guard.note_failure("c")
        self.assertEqual(self.sleeps, [90.0])
        self.assertEqual(self.guard.consecutive_errors, 0)

    def test_throttle_signal_cooldown_immediate(self) -> None:
        self.guard.note_failure("HTTP Error 429: Too Many Requests")
        self.assertEqual(self.sleeps, [90.0])


class TestYtDlpPacing(unittest.TestCase):
    def test_pacing_from_config(self) -> None:
        cfg = RateLimitConfig(sleep_between_requests=2.5, max_sleep_between_requests=5.0)
        pacing = pacing_from_rate_limit(cfg)
        self.assertEqual(pacing.sleep_requests, 2.5)
        self.assertEqual(pacing.max_sleep_interval, 5.0)

    def test_cmd_includes_sleep_requests_not_spoof_headers(self) -> None:
        # find_ytdlp may fail if binary missing — build via known path shape
        import shutil

        if not shutil.which("yt-dlp"):
            self.skipTest("yt-dlp not installed")
        cmd = build_ytdlp_cmd(
            "https://www.youtube.com/@x",
            kind="youtube_channel",
            pacing=YtDlpPacing(sleep_requests=2.0, max_sleep_interval=4.0),
        )
        self.assertIn("--sleep-requests", cmd)
        self.assertIn("2.000", cmd)
        joined = " ".join(cmd).lower()
        # Explicitly assert we do not inject spoofing / evasion flags
        for banned in (
            "--user-agent",
            "--add-header",
            "user-agent:",
            "--impersonate",
            "--geo-bypass-country",
        ):
            self.assertNotIn(banned, joined)


class TestRateLimitConfigFromUniversal(unittest.TestCase):
    def test_parses_nested(self) -> None:
        universal = {
            "youtube": {
                "rate_limit": {
                    "sleep_between_requests": 1.5,
                    "cooldown_seconds": 60,
                    "max_consecutive_errors": 5,
                }
            }
        }
        cfg = RateLimitConfig.from_universal(universal)
        self.assertEqual(cfg.sleep_between_requests, 1.5)
        self.assertEqual(cfg.cooldown_seconds, 60)
        self.assertEqual(cfg.max_consecutive_errors, 5)

    def test_defaults_when_missing(self) -> None:
        cfg = RateLimitConfig.from_universal({})
        self.assertGreaterEqual(cfg.sleep_between_requests, 0)
        self.assertGreater(cfg.cooldown_seconds, 0)


if __name__ == "__main__":
    unittest.main()
