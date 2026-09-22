"""Repository paths. Single source of truth for standard locations."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = REPO_ROOT / "config"
SCHEMAS_DIR = REPO_ROOT / "schemas"
TEMPLATES_DIR = REPO_ROOT / "templates"
CORE_DIR = REPO_ROOT / "core"
MANAGER_DIR = REPO_ROOT / "manager"
TESTS_DIR = REPO_ROOT / "tests"
APPS_DIR = REPO_ROOT / "apps"
DATA_DIR = REPO_ROOT / "data"
LOGS_DIR = REPO_ROOT / "logs"
REPORTS_DIR = REPO_ROOT / "reports"

UNIVERSAL_CONFIG = CONFIG_DIR / "universal.json"
REGISTRY_CONFIG = CONFIG_DIR / "ustaz_registry.json"
USTAZ_CONFIG_DIR = CONFIG_DIR / "ustaz"

UNIVERSAL_SCHEMA = SCHEMAS_DIR / "universal.schema.json"
REGISTRY_SCHEMA = SCHEMAS_DIR / "registry.schema.json"
USTAZ_SCHEMA = SCHEMAS_DIR / "ustaz.schema.json"

USTAZ_TEMPLATE = TEMPLATES_DIR / "config" / "ustaz.json"
SITE_TEMPLATE_DIR = TEMPLATES_DIR / "site" / "default"

DEFAULT_DATABASE = DATA_DIR / "database" / "archive.db"
RAW_DIR = DATA_DIR / "raw"
NORMALIZED_DIR = DATA_DIR / "normalized"
GENERATED_DIR = DATA_DIR / "generated"


def ensure_standard_dirs() -> None:
    """Create runtime directories that are safe to recreate."""
    for path in (
        CONFIG_DIR,
        USTAZ_CONFIG_DIR,
        SCHEMAS_DIR,
        APPS_DIR,
        DATA_DIR,
        DEFAULT_DATABASE.parent,
        RAW_DIR,
        NORMALIZED_DIR,
        GENERATED_DIR,
        LOGS_DIR,
        REPORTS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def resolve_repo_path(path_str: str) -> Path:
    """Resolve a config-relative path against the repo root."""
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def local_override_path(ustaz_id: str) -> Path:
    return USTAZ_CONFIG_DIR / f"{ustaz_id}.local.json"
