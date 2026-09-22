"""Registry operations: list, add, create-from-template."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from core import paths
from core.config import ConfigError, UstazEntry, load_registry, save_registry

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def list_entries(include_disabled: bool = True) -> list[UstazEntry]:
    entries = load_registry()
    if include_disabled:
        return entries
    return [e for e in entries if e.enabled]


def get_entry(ustaz_id: str) -> UstazEntry | None:
    for entry in load_registry():
        if entry.id == ustaz_id:
            return entry
    return None


def add_entry(entry: UstazEntry, overwrite: bool = False) -> None:
    if not ID_PATTERN.match(entry.id):
        raise ConfigError(
            f"invalid id '{entry.id}': must match {ID_PATTERN.pattern}"
        )
    entries = load_registry()
    for existing in entries:
        if existing.id == entry.id:
            if not overwrite:
                raise ConfigError(f"registry already contains id '{entry.id}'")
            entries = [e for e in entries if e.id != entry.id]
            break
    entries.append(entry)
    save_registry(entries)


def create_ustaz(
    ustaz_id: str,
    *,
    name: str | None = None,
    slug: str | None = None,
    description: str = "Islamic lectures and educational talks",
    youtube_channels: list[str] | None = None,
    language: str = "ms",
    enabled: bool = True,
) -> UstazEntry:
    """Scaffold config/ustaz/<id>.json from template and register it."""
    if not ID_PATTERN.match(ustaz_id):
        raise ConfigError(
            f"invalid id '{ustaz_id}': must match {ID_PATTERN.pattern} "
            "(lowercase letter, then letters/digits/underscore)"
        )

    config_path = paths.USTAZ_CONFIG_DIR / f"{ustaz_id}.json"
    if config_path.exists():
        raise ConfigError(f"config already exists: {config_path}")

    if get_entry(ustaz_id) is not None:
        raise ConfigError(f"registry already contains id '{ustaz_id}'")

    display_name = name or ustaz_id.replace("_", " ").title()
    slug_value = slug or ustaz_id.replace("_", "-")
    channels = youtube_channels or []
    # JSON-encoded so the template stays valid JSON after substitution
    template = paths.USTAZ_TEMPLATE.read_text(encoding="utf-8")
    rendered = (
        template.replace("{{id}}", ustaz_id)
        .replace("{{name}}", display_name)
        .replace("{{slug}}", slug_value)
        .replace("{{description}}", description)
        .replace("{{language}}", language)
        .replace("{{youtube_channels}}", json.dumps(channels))
    )

    # ensure rendered output is valid JSON before writing
    parsed = json.loads(rendered)
    if parsed.get("id") != ustaz_id:
        raise ConfigError("template render produced mismatched id")

    paths.USTAZ_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(parsed, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    from core.config import validate_ustaz_file

    errors = validate_ustaz_file(config_path)
    if errors:
        config_path.unlink(missing_ok=True)
        raise ConfigError("\n".join(errors))

    entry = UstazEntry(
        id=ustaz_id,
        name=display_name,
        enabled=enabled,
        config=f"config/ustaz/{ustaz_id}.json",
        output=f"apps/{ustaz_id}",
    )
    add_entry(entry)

    app_dir = paths.REPO_ROOT / entry.output
    app_dir.mkdir(parents=True, exist_ok=True)
    return entry


def register_existing(config_path: Path, *, enabled: bool = True) -> UstazEntry:
    """Register an already-present config file that is not in the registry."""
    from core.config import validate_ustaz_file

    if not config_path.is_file():
        raise ConfigError(f"missing config file: {config_path}")
    errors = validate_ustaz_file(config_path)
    if errors:
        raise ConfigError("\n".join(errors))
    data = json.loads(config_path.read_text(encoding="utf-8"))
    ustaz_id = data["id"]
    rel = config_path.resolve().relative_to(paths.REPO_ROOT.resolve())
    entry = UstazEntry(
        id=ustaz_id,
        name=data["identity"]["name"],
        enabled=enabled,
        config=str(rel).replace("\\", "/"),
        output=f"apps/{ustaz_id}",
    )
    add_entry(entry)
    return entry
