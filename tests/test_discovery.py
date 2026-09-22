"""Phase 2 tests: discovery with a mocked adapter (no network)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core import paths
from core.config import load_registry
from core.database import init_db
from core.discovery import DiscoveryService
from core.sources.base import SourceAdapter, SourceListing


def _channel_entries() -> list[dict]:
    return [
        {
            "video_id": "aaaaaaaaaaa",
            "title": "Tazkirah Ramadan",
            "description": "Harian",
            "channel_id": "UC_xxx",
            "channel_name": "Example",
            "playlist_id": None,
            "playlist_title": None,
            "playlist_position": None,
            "upload_date": "2025-03-01",
            "duration": 600,
            "thumbnail_url": "https://img/aaaaaaaaaaa.jpg",
            "web_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        },
        {
            "video_id": "bbbbbbbbbbb",
            "title": "Tafsir Al-Baqarah 001",
            "description": "Tafsir",
            "channel_id": "UC_xxx",
            "channel_name": "Example",
            "playlist_id": None,
            "playlist_title": None,
            "playlist_position": None,
            "upload_date": "2025-03-02",
            "duration": 1200,
            "thumbnail_url": None,
            "web_url": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
        },
    ]


class FakeChannelAdapter(SourceAdapter):
    name = "fake"

    def __init__(self, entries: list[dict] | None = None):
        self.entries = entries if entries is not None else _channel_entries()
        self.calls: list[tuple[str, str]] = []

    def list_source(self, url: str, *, kind: str, limit: int | None = None) -> SourceListing:
        self.calls.append((url, kind))
        entries = self.entries[:limit] if limit else list(self.entries)
        return SourceListing(
            url=url,
            kind=kind,
            entries=entries,
            source_id="UC_xxx",
            source_title="Example",
        )


class FakePlaylistAdapter(SourceAdapter):
    name = "fake"

    def list_source(self, url: str, *, kind: str, limit: int | None = None) -> SourceListing:
        return SourceListing(
            url=url,
            kind=kind,
            entries=[
                {
                    "video_id": "ccccccccccc",
                    "title": "Tafsir Surah Al-Kahf - Episode 1",
                    "description": "",
                    "channel_id": "UC_xxx",
                    "channel_name": "Example",
                    "playlist_id": "PLtafsiralkahf",
                    "playlist_title": "Tafsir Surah Al-Kahf",
                    "playlist_position": 1,
                    "upload_date": "2024-01-01",
                    "duration": 900,
                    "thumbnail_url": None,
                    "web_url": "https://www.youtube.com/watch?v=ccccccccccc",
                },
                {
                    "video_id": "ddddddddddd",
                    "title": "Tafsir Surah Al-Kahf - Episode 2",
                    "description": "",
                    "channel_id": "UC_xxx",
                    "channel_name": "Example",
                    "playlist_id": "PLtafsiralkahf",
                    "playlist_title": "Tafsir Surah Al-Kahf",
                    "playlist_position": 2,
                    "upload_date": "2024-01-08",
                    "duration": 910,
                    "thumbnail_url": None,
                    "web_url": "https://www.youtube.com/watch?v=ddddddddddd",
                },
            ],
            source_id="PLtafsiralkahf",
            source_title="Tafsir Surah Al-Kahf",
        )


class DiscoveryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "archive.db"
        self.raw_dir = Path(self.tmp.name) / "raw"
        self.conn = init_db(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def make_service(self, adapter: SourceAdapter) -> DiscoveryService:
        return DiscoveryService(
            adapter,
            sleep=lambda _s: None,
            rng=lambda: 0.0,
            raw_dir=self.raw_dir,
        )

    def discover(self, adapter: SourceAdapter, **kwargs):
        service = self.make_service(adapter)
        kwargs.setdefault("db_path", self.db_path)
        kwargs.setdefault("spacing_seconds", 0.0)
        kwargs.setdefault("retry_count", 1)
        kwargs.setdefault("backoff_seconds", 0.0)
        return service.discover("ustaz_a", **kwargs)

    def video_count(self) -> int:
        return self.conn.execute("SELECT count(*) FROM video").fetchone()[0]

    def get_video(self, video_id: str):
        self.conn.rollback()
        return self.conn.execute(
            "SELECT * FROM video WHERE ustaz_id = 'ustaz_a' AND video_id = ?",
            (video_id,),
        ).fetchone()


class TestDiscovery(DiscoveryTestBase):
    def test_channel_discovery_inserts_videos(self) -> None:
        report = self.discover(FakeChannelAdapter())
        self.assertEqual(report.sources_ok, 1)
        self.assertEqual(report.videos_new, 2)
        self.assertEqual(report.videos_updated, 0)
        self.assertEqual(self.video_count(), 2)

        row = self.get_video("aaaaaaaaaaa")
        self.assertIsNotNone(row)
        self.assertEqual(row["state"], "METADATA_READY")
        self.assertEqual(row["download_status"], "PENDING")
        self.assertEqual(row["title"], "Tazkirah Ramadan")
        self.assertEqual(row["web_url"], "https://www.youtube.com/watch?v=aaaaaaaaaaa")
        self.assertIsNotNone(row["metadata_hash"])

    def test_duplicate_discovery_is_idempotent(self) -> None:
        adapter = FakeChannelAdapter()
        first = self.discover(adapter)
        second = self.discover(adapter)
        self.assertEqual(first.videos_new, 2)
        self.assertEqual(second.videos_new, 0)
        self.assertEqual(second.videos_unchanged, 2)
        self.assertEqual(self.video_count(), 2)

    def test_title_change_updates_metadata_not_identity(self) -> None:
        self.discover(FakeChannelAdapter())
        adapter = FakeChannelAdapter()
        adapter.entries = [dict(e) for e in _channel_entries()]
        adapter.entries[0]["title"] = "TAZKIRAH RAMADAN 2025"
        report = self.discover(adapter)
        self.assertEqual(report.videos_updated, 1)
        self.assertEqual(report.videos_new, 0)
        self.assertEqual(self.video_count(), 2)
        row = self.get_video("aaaaaaaaaaa")
        self.assertEqual(row["title"], "TAZKIRAH RAMADAN 2025")
        # download state untouched by rediscovery
        self.assertEqual(row["download_status"], "PENDING")

    def test_playlist_fields_and_series_source(self) -> None:
        original = DiscoveryService.sources_from_config
        DiscoveryService.sources_from_config = staticmethod(
            lambda cfg: [
                ("https://www.youtube.com/playlist?list=PLtafsiralkahf", "youtube_playlist")
            ]
        )
        try:
            report = self.discover(FakePlaylistAdapter())
        finally:
            DiscoveryService.sources_from_config = staticmethod(original)
        self.assertEqual(report.videos_new, 2)
        self.assertEqual(report.playlists_touched, 1)
        row = self.get_video("ccccccccccc")
        self.assertEqual(row["playlist_id"], "PLtafsiralkahf")
        self.assertEqual(row["playlist_title"], "Tafsir Surah Al-Kahf")
        self.assertEqual(row["playlist_position"], 1)
        playlist = self.conn.execute(
            "SELECT * FROM playlist WHERE playlist_id = 'PLtafsiralkahf'"
        ).fetchone()
        self.assertIsNotNone(playlist)
        self.assertEqual(playlist["title"], "Tafsir Surah Al-Kahf")

    def test_source_row_persisted(self) -> None:
        self.discover(FakeChannelAdapter())
        row = self.conn.execute(
            "SELECT * FROM source WHERE ustaz_id = 'ustaz_a'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["source_type"], "youtube_channel")

    def test_raw_metadata_written(self) -> None:
        self.discover(FakeChannelAdapter())
        raw = self.raw_dir / "youtube" / "aaaaaaaaaaa.json"
        self.assertTrue(raw.is_file())
        text = raw.read_text(encoding="utf-8")
        self.assertIn("Tazkirah Ramadan", text)

    def test_dry_run_writes_nothing(self) -> None:
        report = self.discover(FakeChannelAdapter(), dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertEqual(report.videos_new, 2)
        self.assertEqual(self.video_count(), 0)
        self.assertFalse((self.raw_dir / "youtube").exists())

    def test_dedup_across_sources_same_video_id(self) -> None:
        # same video discovered via channel then playlist → still one row
        self.discover(FakeChannelAdapter())
        # craft playlist adapter that re-lists first channel video
        class Revisit(SourceAdapter):
            def list_source(self, url, *, kind, limit=None):
                return SourceListing(
                    url=url,
                    kind=kind,
                    entries=[
                        {
                            **_channel_entries()[0],
                            "playlist_id": "PL_revisit",
                            "playlist_title": "Revisit",
                            "playlist_position": 1,
                        }
                    ],
                )

        report = self.discover(Revisit())
        self.assertEqual(self.video_count(), 2)
        self.assertEqual(report.videos_new, 0)
        row = self.get_video("aaaaaaaaaaa")
        self.assertEqual(row["playlist_id"], "PL_revisit")

    def test_failed_source_is_nonfatal_with_other_success(self) -> None:
        class Mixed(SourceAdapter):
            def list_source(self, url, *, kind, limit=None):
                if "bad" in url:
                    return SourceListing(url=url, kind=kind, error="boom")
                return FakeChannelAdapter().list_source(url, kind=kind, limit=limit)

        # ustaz_a only has one channel; simulate by monkeypatching sources
        original = DiscoveryService.sources_from_config
        DiscoveryService.sources_from_config = staticmethod(
            lambda cfg: [
                ("https://www.youtube.com/@example", "youtube_channel"),
                ("https://www.youtube.com/bad", "youtube_channel"),
            ]
        )
        try:
            report = self.discover(Mixed())
        finally:
            DiscoveryService.sources_from_config = staticmethod(original)
        self.assertEqual(report.sources_ok, 1)
        self.assertEqual(report.sources_failed, 1)
        self.assertEqual(report.videos_new, 2)
        self.assertTrue(any("boom" in e for e in report.errors))


class TestNormalizeFlat(unittest.TestCase):
    def test_normalize_extracts_video_id_and_date(self) -> None:
        from core.sources.youtube import normalize_flat_entry

        raw = {
            "id": "xyzABC12345",
            "title": "Hello",
            "duration": 12.5,
            "upload_date": "20240115",
            "channel_id": "UC1",
            "channel": "Chan",
        }
        out = normalize_flat_entry(raw, position=3)
        assert out is not None
        self.assertEqual(out["video_id"], "xyzABC12345")
        self.assertEqual(out["upload_date"], "2024-01-15")
        self.assertEqual(out["duration"], 12)
        self.assertEqual(out["playlist_position"], 3)
        self.assertEqual(
            out["web_url"], "https://www.youtube.com/watch?v=xyzABC12345"
        )

    def test_normalize_skips_missing_id(self) -> None:
        from core.sources.youtube import normalize_flat_entry

        self.assertIsNone(normalize_flat_entry({"title": "no id"}))


class TestSpeakerFilter(DiscoveryTestBase):
    def _entries(self) -> list[dict]:
        return [
            {
                "video_id": "aaaaaaaaaaa",
                "title": "Ustaz Qarni Edrus : 77 Cabang Iman",
                "description": "",
                "channel_id": "UC_xxx",
                "channel_name": "PROmediaTAJDID",
                "playlist_id": None,
                "playlist_title": None,
                "playlist_position": None,
                "upload_date": "2026-09-21",
                "duration": 3600,
                "thumbnail_url": None,
                "web_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
            },
            {
                "video_id": "bbbbbbbbbbb",
                "title": "Prof Dr Rozaimi Ramle: Adab Memberi Salam",
                "description": "",
                "channel_id": "UC_xxx",
                "channel_name": "PROmediaTAJDID",
                "playlist_id": None,
                "playlist_title": None,
                "playlist_position": None,
                "upload_date": "2026-09-21",
                "duration": 1800,
                "thumbnail_url": None,
                "web_url": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
            },
        ]

    def _discover_with_speakers(self, names: list[str] | None, **kwargs):
        from unittest import mock

        import core.discovery as discovery_mod

        real_load = discovery_mod.load_ustaz_config

        def patched(ustaz_id: str):
            merged = real_load(ustaz_id)
            content = dict(merged["ustaz"].get("content") or {})
            if names is None:
                content.pop("speaker_names", None)
            else:
                content["speaker_names"] = names
            merged["ustaz"] = dict(merged["ustaz"], content=content)
            return merged

        with mock.patch.object(discovery_mod, "load_ustaz_config", new=patched):
            return self.discover(FakeChannelAdapter(entries=self._entries()), **kwargs)

    def test_matches_speakers_pure(self) -> None:
        from core.discovery import matches_speakers

        entry = {"title": "Ustaz Qarni Edrus : 77 Cabang Iman", "description": ""}
        self.assertTrue(matches_speakers(entry, ["qarni edrus"]))
        self.assertTrue(matches_speakers(entry, ["QARNI EDRUS"]))
        self.assertFalse(matches_speakers(entry, ["rozaimi"]))
        # no filter configured → everything matches (old behavior)
        self.assertTrue(matches_speakers(entry, []))
        self.assertTrue(matches_speakers(entry, None))
        # description also counts; channel name alone never matches
        desc = {"title": "Kuliah Maghrib", "description": "bersama qarni edrus"}
        self.assertTrue(matches_speakers(desc, ["qarni edrus"]))
        chan = {"title": "Kuliah Maghrib", "description": "",
                "channel_name": "PROmediaTAJDID"}
        self.assertFalse(matches_speakers(chan, ["promediatajdid"]))

    def test_filter_skips_other_speakers(self) -> None:
        report = self._discover_with_speakers(["qarni edrus"])
        self.assertEqual(report.videos_new, 1)
        self.assertEqual(report.videos_skipped, 1)
        self.assertEqual(self.video_count(), 1)
        self.assertIsNotNone(self.get_video("aaaaaaaaaaa"))
        self.assertIsNone(self.get_video("bbbbbbbbbbb"))

    def test_no_filter_keeps_old_behavior(self) -> None:
        report = self._discover_with_speakers(None)
        self.assertEqual(report.videos_new, 2)
        self.assertEqual(report.videos_skipped, 0)

    def test_prune_removes_non_matching(self) -> None:
        self._discover_with_speakers(None)  # both stored
        self.assertEqual(self.video_count(), 2)
        report = self._discover_with_speakers(["qarni edrus"], prune=True)
        self.assertEqual(report.videos_pruned, 1)
        self.assertEqual(self.video_count(), 1)
        self.assertIsNotNone(self.get_video("aaaaaaaaaaa"))

    def test_prune_refused_without_filter(self) -> None:
        self._discover_with_speakers(None)
        report = self._discover_with_speakers(None, prune=True)
        self.assertEqual(report.videos_pruned, 0)
        self.assertTrue(any("speaker_names" in e for e in report.errors))
        self.assertEqual(self.video_count(), 2)  # nothing deleted

    def test_prune_dry_run_counts_without_deleting(self) -> None:
        self._discover_with_speakers(None)
        report = self._discover_with_speakers(["qarni edrus"], prune=True, dry_run=True)
        self.assertEqual(report.videos_pruned, 1)
        self.assertEqual(self.video_count(), 2)


class TestDiscoverCLI(unittest.TestCase):
    def test_discover_uses_config_validation(self) -> None:
        from manager.cli import main

        # valid config + stub adapter path: CLI would hit network — only ensure
        # the command exists and rejects missing target with usage exit 2
        rc = main(["discover"])
        self.assertEqual(rc, 2)

    def test_sync_is_alias_registered(self) -> None:
        from manager.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["sync", "ustaz_a", "--dry-run", "--fast"])
        self.assertEqual(args.command, "sync")
        self.assertTrue(args.dry_run)
        self.assertTrue(args.fast)

    def test_prune_flag_registered(self) -> None:
        from manager.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["discover", "ustaz_a", "--prune"])
        self.assertTrue(args.prune)
        args = parser.parse_args(["discover", "ustaz_a"])
        self.assertFalse(args.prune)


if __name__ == "__main__":
    unittest.main()
