"""Phase 4 tests: deterministic classification + grouping (no network)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.classifier import (
    UNCLASSIFIED,
    Classifier,
    classify_video,
    ensure_category,
    slugify,
)
from core.database import init_db
from core.grouping import Grouper, ensure_series, series_id_for


RULES = [
    {"match": ["tafsir", "surah", "ayat"], "category": "tafsir"},
    {"match": ["fiqh", "hukum"], "category": "fiqh"},
    {"match": ["aqidah", "akidah"], "category": "aqidah"},
]


class TestClassifyPure(unittest.TestCase):
    def test_title_rule(self) -> None:
        r = classify_video(
            {"title": "Tafsir Surah Al-Kahf", "description": ""},
            rules=RULES,
        )
        self.assertEqual(r.category, "tafsir")
        self.assertEqual(r.matched_by, "title")

    def test_playlist_priority_before_title(self) -> None:
        r = classify_video(
            {
                "title": "Random talk",
                "playlist_title": "Kuliah Fiqh Weekend",
                "description": "",
            },
            rules=RULES,
        )
        self.assertEqual(r.category, "fiqh")
        self.assertEqual(r.matched_by, "playlist")

    def test_description_fallback(self) -> None:
        r = classify_video(
            {"title": "Talk", "description": "about hukum waris"},
            rules=RULES,
        )
        self.assertEqual(r.category, "fiqh")
        self.assertEqual(r.matched_by, "description")

    def test_default_category_when_no_rule(self) -> None:
        r = classify_video(
            {"title": "Talk", "description": ""},
            rules=RULES,
            default_category="ceramah",
        )
        self.assertEqual(r.category, "ceramah")
        self.assertEqual(r.matched_by, "default")

    def test_unclassified_bucket(self) -> None:
        r = classify_video({"title": "Talk", "description": ""}, rules=RULES)
        self.assertEqual(r.category, UNCLASSIFIED)
        self.assertEqual(r.matched_by, "unclassified")

    def test_case_insensitive(self) -> None:
        r = classify_video(
            {"title": "TAFSIR RINGKAS", "description": ""}, rules=RULES
        )
        self.assertEqual(r.category, "tafsir")

    def test_first_matching_rule_wins(self) -> None:
        rules = [
            {"match": ["special"], "category": "first"},
            {"match": ["special"], "category": "second"},
        ]
        r = classify_video({"title": "special topic", "description": ""}, rules=rules)
        self.assertEqual(r.category, "first")
        self.assertEqual(r.rule_index, 0)


class TestSlug(unittest.TestCase):
    def test_slugify(self) -> None:
        self.assertEqual(slugify("Tafsir Al-Kahf!"), "tafsir-al-kahf")
        self.assertEqual(slugify("  "), "uncategorized")


class ClassifierDBBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "archive.db"
        self.conn = init_db(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def insert_video(self, video_id: str, **kw) -> None:
        self.conn.execute(
            """
            INSERT INTO video (
                ustaz_id, video_id, title, original_title, description,
                playlist_id, playlist_title, playlist_position,
                state, download_status, classification_status,
                first_seen_at, last_seen_at, metadata_hash
            ) VALUES ('ustaz_a', ?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETED', 'PENDING', ?, ?, 'h')
            """,
            (
                video_id,
                kw.get("title", "Talk"),
                kw.get("title", "Talk"),
                kw.get("description", ""),
                kw.get("playlist_id"),
                kw.get("playlist_title"),
                kw.get("playlist_position"),
                kw.get("state", "DOWNLOADED"),
                "2024-01-01T00:00:00Z",
                "2024-01-01T00:00:00Z",
            ),
        )
        self.conn.commit()

    def video(self, video_id: str):
        self.conn.rollback()
        return self.conn.execute(
            "SELECT * FROM video WHERE ustaz_id='ustaz_a' AND video_id=?",
            (video_id,),
        ).fetchone()

    def sample_config(self) -> dict:
        return {
            "ustaz": {
                "id": "ustaz_a",
                "identity": {"name": "X", "slug": "x"},
                "sources": {"youtube_channels": ["https://a"], "youtube_playlists": []},
                "content": {"languages": ["ms"], "default_category": "ceramah"},
                "classification": {
                    "enabled": True,
                    "rules": {
                        "playlist_priority": True,
                        "title_priority": True,
                        "description_priority": True,
                    },
                    "match_rules": RULES,
                },
            },
            "universal": {},
        }


class TestClassifierDB(ClassifierDBBase):
    def test_classifies_pending_videos(self) -> None:
        self.insert_video("aaaaaaaaaaa", title="Tafsir Al-Kahf")
        self.insert_video("bbbbbbbbbbb", title="Random", description="about hukum")
        self.insert_video("ccccccccccc", title="Unclear talk")
        report = Classifier().run(
            "ustaz_a", db_path=self.db_path, config=self.sample_config()
        )
        # default_category "ceramah" applies when no rule matches
        self.assertEqual(report.classified, 3)
        self.assertEqual(report.unclassified, 0)
        self.assertEqual(self.video("aaaaaaaaaaa")["category"], "tafsir")
        self.assertEqual(self.video("aaaaaaaaaaa")["classification_status"], "CLASSIFIED")
        self.assertEqual(self.video("aaaaaaaaaaa")["state"], "CLASSIFIED")
        self.assertEqual(self.video("bbbbbbbbbbb")["category"], "fiqh")
        self.assertEqual(self.video("ccccccccccc")["category"], "ceramah")
        self.assertEqual(
            self.video("ccccccccccc")["classification_status"], "CLASSIFIED"
        )

    def test_unclassified_without_default(self) -> None:
        self.insert_video("ccccccccccc", title="Unclear talk")
        cfg = self.sample_config()
        cfg["ustaz"]["content"]["default_category"] = None
        report = Classifier().run("ustaz_a", db_path=self.db_path, config=cfg)
        self.assertEqual(report.unclassified, 1)
        row = self.video("ccccccccccc")
        self.assertEqual(row["category"], UNCLASSIFIED)
        self.assertEqual(row["classification_status"], "UNCLASSIFIED")

    def test_skips_already_classified(self) -> None:
        self.insert_video("aaaaaaaaaaa", title="Tafsir")
        cfg = self.sample_config()
        Classifier().run("ustaz_a", db_path=self.db_path, config=cfg)
        report = Classifier().run("ustaz_a", db_path=self.db_path, config=cfg)
        self.assertEqual(report.scanned, 0)

    def test_dry_run_no_writes(self) -> None:
        self.insert_video("aaaaaaaaaaa", title="Tafsir")
        report = Classifier().run(
            "ustaz_a", dry_run=True, db_path=self.db_path, config=self.sample_config()
        )
        self.assertEqual(report.classified, 1)
        self.assertIsNone(self.video("aaaaaaaaaaa")["category"])
        self.assertEqual(
            self.video("aaaaaaaaaaa")["classification_status"], "PENDING"
        )

    def test_category_join_written(self) -> None:
        self.insert_video("aaaaaaaaaaa", title="Tafsir")
        Classifier().run("ustaz_a", db_path=self.db_path, config=self.sample_config())
        row = self.conn.execute(
            """
            SELECT c.slug FROM video_category vc
            JOIN category c ON c.id = vc.category_id
            WHERE vc.video_id = 'aaaaaaaaaaa'
            """
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["slug"], "tafsir")

    def test_disabled_config_is_noop(self) -> None:
        self.insert_video("aaaaaaaaaaa", title="Tafsir")
        cfg = self.sample_config()
        cfg["ustaz"]["classification"]["enabled"] = False
        report = Classifier().run("ustaz_a", db_path=self.db_path, config=cfg)
        self.assertEqual(report.scanned, 0)
        self.assertTrue(report.errors)


class TestGrouperDB(ClassifierDBBase):
    def test_playlist_creates_series(self) -> None:
        self.insert_video(
            "aaaaaaaaaaa",
            title="Ep 1",
            playlist_id="PLabc",
            playlist_title="Tafsir Surah Al-Kahf",
            playlist_position=1,
        )
        self.insert_video(
            "bbbbbbbbbbb",
            title="Ep 2",
            playlist_id="PLabc",
            playlist_title="Tafsir Surah Al-Kahf",
            playlist_position=2,
        )
        report = Grouper().run("ustaz_a", db_path=self.db_path, config=self.sample_config())
        self.assertEqual(report.videos_grouped, 2)
        self.assertEqual(report.series_created, 1)
        series = self.conn.execute(
            "SELECT * FROM series WHERE ustaz_id='ustaz_a'"
        ).fetchall()
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["slug"], "tafsir-surah-al-kahf")
        self.assertEqual(series[0]["title"], "Tafsir Surah Al-Kahf")
        v = self.video("aaaaaaaaaaa")
        self.assertEqual(v["series_id"], series[0]["series_id"])
        self.assertEqual(v["state"], "GROUPED")
        join = self.conn.execute(
            "SELECT * FROM video_series WHERE video_id='aaaaaaaaaaa'"
        ).fetchone()
        self.assertIsNotNone(join)
        self.assertEqual(join["position"], 1)

    def test_playlist_is_source_series_is_archive(self) -> None:
        self.insert_video(
            "aaaaaaaaaaa",
            title="Ep 1",
            playlist_id="PLabc",
            playlist_title="2024 Kuliah Maghrib",
        )
        Grouper().run("ustaz_a", db_path=self.db_path, config=self.sample_config())
        v = self.video("aaaaaaaaaaa")
        # source playlist preserved separately from archive series
        self.assertEqual(v["playlist_id"], "PLabc")
        self.assertIsNotNone(v["series_id"])
        self.assertNotEqual(v["series_id"], "PLabc")

    def test_no_playlist_leaves_ungrouped(self) -> None:
        self.insert_video("aaaaaaaaaaa", title="Standalone talk")
        report = Grouper().run("ustaz_a", db_path=self.db_path, config=self.sample_config())
        self.assertEqual(report.videos_grouped, 0)
        self.assertIsNone(self.video("aaaaaaaaaaa")["series_id"])

    def test_dry_run(self) -> None:
        self.insert_video(
            "aaaaaaaaaaa",
            title="Ep 1",
            playlist_id="PLabc",
            playlist_title="Series",
        )
        report = Grouper().run(
            "ustaz_a", dry_run=True, db_path=self.db_path, config=self.sample_config()
        )
        self.assertEqual(report.videos_grouped, 1)
        self.assertIsNone(self.video("aaaaaaaaaaa")["series_id"])
        count = self.conn.execute("SELECT count(*) AS c FROM series").fetchone()["c"]
        self.assertEqual(count, 0)

    def test_second_run_scans_nothing_new(self) -> None:
        self.insert_video(
            "aaaaaaaaaaa",
            title="Ep 1",
            playlist_id="PLabc",
            playlist_title="Series",
        )
        Grouper().run("ustaz_a", db_path=self.db_path, config=self.sample_config())
        report = Grouper().run(
            "ustaz_a", db_path=self.db_path, config=self.sample_config()
        )
        self.assertEqual(report.scanned, 0)
        self.assertEqual(report.videos_grouped, 0)

    def test_regroup_moves_video_to_rule_series(self) -> None:
        self.insert_video(
            "aaaaaaaaaaa",
            title="77 Cabang Iman",
            playlist_id="PLmega",
            playlist_title="Mega Playlist",
        )
        Grouper().run("ustaz_a", db_path=self.db_path, config=self.sample_config())
        first_series = self.video("aaaaaaaaaaa")["series_id"]
        self.assertIsNotNone(first_series)
        cfg = self.sample_config()
        cfg["ustaz"]["classification"]["series_rules"] = [
            {"match": ["77 cabang iman"], "series": "77 Cabang Iman"}
        ]
        report = Grouper().run(
            "ustaz_a", db_path=self.db_path, config=cfg, regroup=True
        )
        self.assertEqual(report.videos_regrouped, 1)
        row = self.video("aaaaaaaaaaa")
        self.assertNotEqual(row["series_id"], first_series)
        series = self.conn.execute(
            "SELECT slug FROM series WHERE series_id = ?", (row["series_id"],)
        ).fetchone()
        self.assertEqual(series["slug"], "77-cabang-iman")
        # stale join removed, orphan playlist-series deleted
        joins = self.conn.execute(
            "SELECT count(*) AS c FROM video_series WHERE video_id='aaaaaaaaaaa'"
        ).fetchone()["c"]
        self.assertEqual(joins, 1)
        gone = self.conn.execute(
            "SELECT count(*) AS c FROM series WHERE series_id = ?", (first_series,)
        ).fetchone()["c"]
        self.assertEqual(gone, 0)

    def test_regroup_dry_run_changes_nothing(self) -> None:
        self.insert_video(
            "aaaaaaaaaaa",
            title="77 Cabang Iman",
            playlist_id="PLmega",
            playlist_title="Mega Playlist",
        )
        Grouper().run("ustaz_a", db_path=self.db_path, config=self.sample_config())
        before = self.video("aaaaaaaaaaa")["series_id"]
        cfg = self.sample_config()
        cfg["ustaz"]["classification"]["series_rules"] = [
            {"match": ["77 cabang iman"], "series": "77 Cabang Iman"}
        ]
        report = Grouper().run(
            "ustaz_a", dry_run=True, db_path=self.db_path, config=cfg, regroup=True
        )
        self.assertEqual(report.videos_regrouped, 1)
        self.assertEqual(self.video("aaaaaaaaaaa")["series_id"], before)

    def test_series_id_stable(self) -> None:
        self.assertEqual(series_id_for("u", "slug"), series_id_for("u", "slug"))
        self.assertNotEqual(series_id_for("u", "slug"), series_id_for("v", "slug"))

    def test_series_rules_from_title(self) -> None:
        cfg = self.sample_config()
        cfg["ustaz"]["classification"]["series_rules"] = [
            {"match": ["ramadan"], "series": "Tazkirah Ramadan"}
        ]
        self.insert_video("aaaaaaaaaaa", title="Tazkirah Ramadan Hari 1")
        Grouper().run("ustaz_a", db_path=self.db_path, config=cfg)
        series = self.conn.execute("SELECT * FROM series").fetchall()
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["slug"], "tazkirah-ramadan")


class TestEnsureHelpers(ClassifierDBBase):
    def test_ensure_category_idempotent(self) -> None:
        a = ensure_category(self.conn, "ustaz_a", "Fiqh")
        b = ensure_category(self.conn, "ustaz_a", "Fiqh")
        self.assertEqual(a, b)

    def test_ensure_series_idempotent(self) -> None:
        sid1, created1 = ensure_series(
            self.conn, ustaz_id="ustaz_a", slug="s", title="S"
        )
        sid2, created2 = ensure_series(
            self.conn, ustaz_id="ustaz_a", slug="s", title="S"
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(sid1, sid2)


class TestCLIPhase4(unittest.TestCase):
    def test_commands_registered(self) -> None:
        from manager.cli import build_parser

        parser = build_parser()
        a = parser.parse_args(["classify", "ustaz_a", "--dry-run"])
        self.assertEqual(a.command, "classify")
        b = parser.parse_args(["group", "--all", "--dry-run"])
        self.assertEqual(b.command, "group")
        c = parser.parse_args(["classify", "ustaz_a", "--reclassify"])
        self.assertTrue(c.reclassify)
        g = parser.parse_args(["group", "ustaz_a", "--regroup"])
        self.assertTrue(g.regroup)

    def test_build_command_succeeds(self) -> None:
        from manager.cli import main

        self.assertEqual(main(["build", "ustaz_a"]), 0)


if __name__ == "__main__":
    unittest.main()
