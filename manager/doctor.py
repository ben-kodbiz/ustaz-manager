"""Doctor: environment + configuration health checks (Phase 1 gate)."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from core import database, paths
from core.config import ConfigError, load_registry, load_universal, validate_all
from core.logging_setup import setup_logging


@dataclass
class CheckResult:
    name: str
    status: str  # OK | WARN | FAIL
    detail: str

    @property
    def passed(self) -> bool:
        return self.status != "FAIL"


def _check_python() -> CheckResult:
    version = sys.version_info
    if version < (3, 10):
        return CheckResult("python", "FAIL", f"3.10+ required, found {sys.version.split()[0]}")
    return CheckResult("python", "OK", sys.version.split()[0])


def _check_dirs() -> CheckResult:
    required = [
        paths.CONFIG_DIR,
        paths.USTAZ_CONFIG_DIR,
        paths.SCHEMAS_DIR,
        paths.TEMPLATES_DIR,
        paths.TESTS_DIR,
        paths.CORE_DIR,
        paths.MANAGER_DIR,
    ]
    missing = [str(p.relative_to(paths.REPO_ROOT)) for p in required if not p.is_dir()]
    if missing:
        return CheckResult("directories", "FAIL", "missing: " + ", ".join(missing))
    return CheckResult("directories", "OK", f"{len(required)} present")


def _check_files() -> CheckResult:
    required = [
        paths.UNIVERSAL_CONFIG,
        paths.REGISTRY_CONFIG,
        paths.UNIVERSAL_SCHEMA,
        paths.REGISTRY_SCHEMA,
        paths.USTAZ_SCHEMA,
        paths.USTAZ_TEMPLATE,
        paths.REPO_ROOT / "archive.zsh",
    ]
    missing = [str(p.relative_to(paths.REPO_ROOT)) for p in required if not p.is_file()]
    if missing:
        return CheckResult("files", "FAIL", "missing: " + ", ".join(missing))
    return CheckResult("files", "OK", f"{len(required)} present")


def _check_configs() -> CheckResult:
    problems = validate_all()
    if problems:
        return CheckResult("config-validation", "FAIL", f"{len(problems)} problem(s)")
    try:
        entries = load_registry()
        load_universal()
    except ConfigError as exc:
        return CheckResult("config-validation", "FAIL", str(exc))
    enabled = sum(1 for e in entries if e.enabled)
    return CheckResult(
        "config-validation",
        "OK",
        f"{len(entries)} registry entr(ies), {enabled} enabled, all schemas valid",
    )


def _check_database() -> CheckResult:
    try:
        conn = database.init_db()
    except Exception as exc:  # noqa: BLE001 - doctor must surface any DB error
        return CheckResult("database", "FAIL", f"{exc}")
    try:
        has_fts = database.has_fts5(conn)
        conn.execute("SELECT count(*) FROM meta")
        db_path = database.database_path()
        rel = (
            str(db_path.relative_to(paths.REPO_ROOT))
            if db_path.is_relative_to(paths.REPO_ROOT)
            else str(db_path)
        )
        detail = f"{rel} initialized"
        if has_fts:
            detail += ", FTS5 available"
        else:
            detail += ", FTS5 unavailable (WARN)"
        conn.close()
        status = "OK" if has_fts else "WARN"
        return CheckResult("database", status, detail)
    except Exception as exc:  # noqa: BLE001
        return CheckResult("database", "FAIL", str(exc))


def _check_logging() -> CheckResult:
    try:
        setup_logging()
        if not paths.LOGS_DIR.is_dir():
            return CheckResult("logging", "FAIL", "logs/ not created")
        probe = paths.LOGS_DIR / "manager.log"
        if not probe.exists():
            probe.touch()
        return CheckResult("logging", "OK", "logs/ writable, manager.log ready")
    except OSError as exc:
        return CheckResult("logging", "FAIL", str(exc))


def _check_ytdlp() -> CheckResult:
    path = shutil.which("yt-dlp")
    if path:
        return CheckResult("yt-dlp", "OK", path)
    return CheckResult(
        "yt-dlp", "WARN", "not found on PATH (required for discovery / Phase 2)"
    )


def _check_writable_data() -> CheckResult:
    try:
        paths.ensure_standard_dirs()
        probe = paths.DATA_DIR / ".doctor_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return CheckResult("data-dirs", "OK", "data/ and logs/ writable")
    except OSError as exc:
        return CheckResult("data-dirs", "FAIL", str(exc))


def run_doctor() -> tuple[list[CheckResult], bool]:
    checks = [
        _check_python(),
        _check_dirs(),
        _check_files(),
        _check_configs(),
        _check_database(),
        _check_logging(),
        _check_writable_data(),
        _check_ytdlp(),
    ]
    ok = all(c.passed for c in checks)
    return checks, ok


def format_report(checks: list[CheckResult], ok: bool) -> str:
    lines = ["Archive Manager — doctor", "─" * 40]
    for check in checks:
        lines.append(f"[{check.status:4}] {check.name}: {check.detail}")
    lines.append("─" * 40)
    lines.append("RESULT: " + ("OK" if ok else "FAILED"))
    return "\n".join(lines)
