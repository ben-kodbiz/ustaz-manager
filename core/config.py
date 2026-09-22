"""Load, inherit, and validate configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import paths
from core.validation import validate


class ConfigError(Exception):
    pass


def load_json(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc


def deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def format_validation_errors(label: str, errors: list[str]) -> str:
    return f"ERROR:\n{label}\n\n" + "\n".join(errors)


@dataclass
class UstazEntry:
    id: str
    name: str
    enabled: bool
    config: str
    output: str


@dataclass
class LoadedConfig:
    universal: dict
    registry_entries: list[UstazEntry]
    ustaz_configs: dict[str, dict] = field(default_factory=dict)


def load_schema(schema_path: Path) -> dict:
    return load_json(schema_path)


def validate_document(instance: Any, schema_path: Path, label: str) -> None:
    schema = load_schema(schema_path)
    errors = validate(instance, schema)
    if errors:
        raise ConfigError(format_validation_errors(label, errors))


def check_ustaz_sources(cfg: dict, label: str) -> None:
    sources = cfg.get("sources") or {}
    channels = sources.get("youtube_channels") or []
    playlists = sources.get("youtube_playlists") or []
    if not channels and not playlists:
        raise ConfigError(
            format_validation_errors(
                label,
                [
                    "sources: at least one of youtube_channels or "
                    "youtube_playlists is required"
                ],
            )
        )


def load_universal(path: Path | None = None) -> dict:
    path = path or paths.UNIVERSAL_CONFIG
    data = load_json(path)
    validate_document(data, paths.UNIVERSAL_SCHEMA, path.name)
    return data


def load_registry(path: Path | None = None) -> list[UstazEntry]:
    path = path or paths.REGISTRY_CONFIG
    data = load_json(path)
    validate_document(data, paths.REGISTRY_SCHEMA, path.name)
    entries: list[UstazEntry] = []
    seen: set[str] = set()
    for raw in data["ustaz"]:
        entry = UstazEntry(
            id=raw["id"],
            name=raw["name"],
            enabled=bool(raw["enabled"]),
            config=raw["config"],
            output=raw["output"],
        )
        if entry.id in seen:
            raise ConfigError(
                format_validation_errors(
                    path.name, [f"duplicate registry id: {entry.id}"]
                )
            )
        seen.add(entry.id)
        entries.append(entry)
    return entries


def save_registry(entries: list[UstazEntry], path: Path | None = None) -> None:
    path = path or paths.REGISTRY_CONFIG
    payload = {
        "ustaz": [
            {
                "id": e.id,
                "name": e.name,
                "enabled": e.enabled,
                "config": e.config,
                "output": e.output,
            }
            for e in entries
        ]
    }
    validate_document(payload, paths.REGISTRY_SCHEMA, path.name)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def load_ustaz_config(ustaz_id: str) -> dict:
    """Load universal + ustaz config (+ optional local override), validated."""
    universal = load_universal()
    config_path = paths.USTAZ_CONFIG_DIR / f"{ustaz_id}.json"
    if not config_path.is_file():
        raise ConfigError(f"missing ustaz config: {config_path}")
    data = load_json(config_path)
    label = config_path.name
    validate_document(data, paths.USTAZ_SCHEMA, label)
    check_ustaz_sources(data, label)

    override_path = paths.local_override_path(ustaz_id)
    if override_path.is_file():
        override = load_json(override_path)
        validate_document(override, paths.USTAZ_SCHEMA, override_path.name)
        data = deep_merge(data, override)

    if data.get("id") != ustaz_id:
        raise ConfigError(
            format_validation_errors(
                label, [f"id: must be '{ustaz_id}', found '{data.get('id')}'"]
            )
        )

    return {"universal": universal, "ustaz": data}


def validate_ustaz_file(config_path: Path) -> list[str]:
    """Return validation errors for one ustaz config file (empty = ok)."""
    try:
        data = load_json(config_path)
    except ConfigError as exc:
        return [str(exc)]
    schema = load_schema(paths.USTAZ_SCHEMA)
    errors = validate(data, schema)
    if errors:
        return [format_validation_errors(config_path.name, errors)]
    try:
        check_ustaz_sources(data, config_path.name)
    except ConfigError as exc:
        return [str(exc)]
    if data.get("id") != config_path.stem:
        return [
            format_validation_errors(
                config_path.name,
                [f"id: must be '{config_path.stem}', found '{data.get('id')}'"],
            )
        ]
    return []


def validate_all(entries: list[UstazEntry] | None = None) -> list[str]:
    """Validate universal, registry, and enabled per-ustaz configs."""
    problems: list[str] = []

    try:
        load_universal()
    except (ConfigError, OSError) as exc:
        problems.append(str(exc))

    try:
        entries = load_registry() if entries is None else entries
    except (ConfigError, OSError) as exc:
        problems.append(str(exc))
        entries = []

    for entry in entries:
        if not entry.enabled:
            continue
        config_path = paths.resolve_repo_path(entry.config)
        if not config_path.is_file():
            problems.append(
                format_validation_errors(
                    entry.config, ["file not found for registry entry"]
                )
            )
            continue
        problems.extend(validate_ustaz_file(config_path))

    unique: list[str] = []
    seen: set[str] = set()
    for problem in problems:
        if problem and problem not in seen:
            seen.add(problem)
            unique.append(problem)
    return unique


def load_all(entries: list[UstazEntry] | None = None) -> LoadedConfig:
    universal = load_universal()
    entries = load_registry() if entries is None else entries
    configs: dict[str, dict] = {}
    for entry in entries:
        if not entry.enabled:
            continue
        configs[entry.id] = load_ustaz_config(entry.id)["ustaz"]
    return LoadedConfig(
        universal=universal, registry_entries=entries, ustaz_configs=configs
    )
