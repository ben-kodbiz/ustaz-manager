"""Phase 1 tests: structured logging writes required fields."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from core import paths
from core.logging_setup import log_event, setup_logging


class TestLogging(unittest.TestCase):
    def test_structured_line_has_required_fields(self) -> None:
        setup_logging()
        log_event(
            "manager",
            "doctor ran",
            ustaz_id="ustaz_a",
            video_id="abc",
            job_id=1,
        )
        log_path = paths.LOGS_DIR / "manager.log"
        self.assertTrue(log_path.is_file())
        lines = [
            line
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(lines)
        last = json.loads(lines[-1])
        for key in ("timestamp", "stage", "message", "ustaz_id", "video_id", "job_id"):
            self.assertIn(key, last)
        self.assertEqual(last["stage"], "manager")
        self.assertEqual(last["message"], "doctor ran")


if __name__ == "__main__":
    unittest.main()
