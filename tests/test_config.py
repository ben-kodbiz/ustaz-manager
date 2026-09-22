"""Phase 1 tests: config load, schema validation, inheritance, registry."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core import paths
from core.config import (
    ConfigError,
    deep_merge,
    format_validation_errors,
    load_all,
    load_registry,
    load_ustaz_config,
    load_universal,
    validate_all,
    validate_ustaz_file,
)
from core.validation import validate

REPO = paths.REPO_ROOT


class TestUniversalConfig(unittest.TestCase):
    def test_loads_and_validates(self) -> None:
        data = load_universal()
        self.assertEqual(data["project"]["name"], "ustaz-archive-factory")
        self.assertIn("storage", data)

    def test_no_ustaz_specific_keys(self) -> None:
        data = load_universal()
        # universal config must not carry Ustaz identity/sources
        for forbidden in ("ustaz", "identity", "sources", "persona"):
            self.assertNotIn(forbidden, data)


class TestRegistry(unittest.TestCase):
    def test_loads_entries(self) -> None:
        entries = load_registry()
        self.assertGreaterEqual(len(entries), 1)
        ids = [e.id for e in entries]
        self.assertEqual(len(ids), len(set(ids)), "registry ids must be unique")
        self.assertIn("ustaz_a", ids)

    def test_entry_paths_exist(self) -> None:
        for entry in load_registry():
            config_path = paths.resolve_repo_path(entry.config)
            self.assertTrue(config_path.is_file(), f"missing {entry.config}")


class TestUstazConfig(unittest.TestCase):
    def test_load_ustaz_a(self) -> None:
        merged = load_ustaz_config("ustaz_a")
        self.assertEqual(merged["ustaz"]["id"], "ustaz_a")
        self.assertIn("universal", merged)

    def test_missing_sources_rejected(self) -> None:
        schema = json.loads(paths.USTAZ_SCHEMA.read_text(encoding="utf-8"))
        bad = {
            "id": "x",
            "identity": {"name": "X", "slug": "x"},
            "sources": {"youtube_channels": [], "youtube_playlists": []},
        }
        # schema (any_array_present) and semantic check both reject empty sources
        errors = validate(bad, schema)
        self.assertTrue(any("at least one" in e for e in errors))
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir=paths.USTAZ_CONFIG_DIR
        ) as fh:
            json.dump(bad, fh)
            tmp_name = Path(fh.name)
        try:
            problems = validate_ustaz_file(tmp_name)
            self.assertTrue(problems)
            self.assertIn("at least one", problems[0])
        finally:
            tmp_name.unlink(missing_ok=True)

    def test_schema_rejects_missing_identity(self) -> None:
        schema = json.loads(paths.USTAZ_SCHEMA.read_text(encoding="utf-8"))
        bad = {"id": "x", "sources": {"youtube_channels": ["https://a"]}}
        errors = validate(bad, schema)
        self.assertTrue(any("identity is required" in e for e in errors))

    def test_id_mismatch_rejected(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, dir=paths.USTAZ_CONFIG_DIR
        ) as fh:
            json.dump(
                {
                    "id": "other_id",
                    "identity": {"name": "X", "slug": "x"},
                    "sources": {"youtube_channels": ["https://a"]},
                },
                fh,
            )
            tmp_name = Path(fh.name)
        try:
            problems = validate_ustaz_file(tmp_name)
            self.assertTrue(any("must be" in p for p in problems))
        finally:
            tmp_name.unlink(missing_ok=True)


class TestDeepMerge(unittest.TestCase):
    def test_nested_override(self) -> None:
        base = {"a": {"x": 1, "y": 2}, "b": 1}
        override = {"a": {"y": 3, "z": 4}}
        merged = deep_merge(base, override)
        self.assertEqual(merged, {"a": {"x": 1, "y": 3, "z": 4}, "b": 1})

    def test_base_not_mutated(self) -> None:
        base = {"a": {"x": 1}}
        deep_merge(base, {"a": {"x": 2}})
        self.assertEqual(base["a"]["x"], 1)


class TestValidationHelpers(unittest.TestCase):
    def test_format_matches_spec_shape(self) -> None:
        text = format_validation_errors(
            "ustaz_a.json", ["sources.youtube_channels is required"]
        )
        self.assertIn("ERROR:", text)
        self.assertIn("ustaz_a.json", text)
        self.assertIn("sources.youtube_channels is required", text)

    def test_validate_type_error(self) -> None:
        errors = validate({"a": "nope"}, {"type": "object", "properties": {"a": {"type": "integer"}}})
        self.assertTrue(errors)

    def test_validate_required(self) -> None:
        errors = validate({}, {"type": "object", "required": ["missing"]})
        self.assertEqual(errors, ["missing is required"])


class TestValidateAll(unittest.TestCase):
    def test_repo_configs_clean(self) -> None:
        problems = validate_all()
        self.assertEqual(problems, [], msg="\n".join(problems))

    def test_load_all_enabled(self) -> None:
        loaded = load_all()
        for entry in loaded.registry_entries:
            if entry.enabled:
                self.assertIn(entry.id, loaded.ustaz_configs)


class TestDoctor(unittest.TestCase):
    def test_doctor_passes(self) -> None:
        from manager.doctor import run_doctor

        checks, ok = run_doctor()
        failed = [c for c in checks if not c.passed]
        self.assertTrue(
            ok,
            msg="doctor failures: "
            + "; ".join(f"{c.name}: {c.detail}" for c in failed),
        )


class TestCLI(unittest.TestCase):
    def test_doctor_exit_zero(self) -> None:
        from manager.cli import main

        self.assertEqual(main(["doctor"]), 0)

    def test_list_exit_zero(self) -> None:
        from manager.cli import main

        self.assertEqual(main(["list"]), 0)

    def test_create_and_list_roundtrip(self) -> None:
        from manager.cli import main
        from manager.registry import get_entry
        from core.config import load_registry, save_registry

        test_id = "ustaz_test_tmp"
        # clean slate
        entries = [e for e in load_registry() if e.id != test_id]
        save_registry(entries)
        config_path = paths.USTAZ_CONFIG_DIR / f"{test_id}.json"
        config_path.unlink(missing_ok=True)

        try:
            rc = main(
                [
                    "create",
                    test_id,
                    "--name",
                    "Temp Ustaz",
                    "--channel",
                    "https://www.youtube.com/@temp",
                ]
            )
            self.assertEqual(rc, 0)
            self.assertIsNotNone(get_entry(test_id))
            self.assertTrue(config_path.is_file())
            self.assertEqual(main(["list"]), 0)
        finally:
            remaining = [e for e in load_registry() if e.id != test_id]
            save_registry(remaining)
            config_path.unlink(missing_ok=True)
            app_dir = paths.REPO_ROOT / "apps" / test_id
            if app_dir.is_dir():
                for child in sorted(app_dir.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink()
                    child.rmdir()

    def test_stage_validates_config(self) -> None:
        from manager.cli import main

        # publish rebuilds + validates — config validated first, no network
        rc = main(["publish", "ustaz_a"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
