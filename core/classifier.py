"""Deterministic classification (rules first; LLM never required).

Priority (todoagent.md §13):
  1. explicit manual rules (match_rules / playlist_priority / title / description)
  2. playlist metadata
  3. title patterns
  4. description
  5. existing series/category defaults
  6. optional LLM (not implemented as hard dependency)
  7. "Unclassified"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from core.config import ConfigError, load_ustaz_config
from core.database import init_db
from core.logging_setup import log_event

UNCLASSIFIED = "Unclassified"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower())
    return text.strip("-") or "uncategorized"


@dataclass
class ClassificationResult:
    category: str
    matched_by: str  # rule | playlist | title | description | default | unclassified
    rule_index: int | None = None


@dataclass
class ClassifyReport:
    ustaz_id: str
    dry_run: bool = False
    scanned: int = 0
    classified: int = 0
    unclassified: int = 0
    already_done: int = 0
    errors: list[str] = field(default_factory=list)


def _normalize_rules(raw_rules: Any) -> list[dict[str, Any]]:
    """Accept match_rules list; tolerate legacy {rules: [...]} shapes."""
    if not isinstance(raw_rules, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        match = item.get("match")
        category = item.get("category")
        if not isinstance(match, list) or not match or not category:
            continue
        out.append(
            {
                "match": [str(m).lower() for m in match if str(m).strip()],
                "category": str(category),
            }
        )
    return out


def classify_video(
    video: dict[str, Any],
    *,
    rules: list[dict[str, Any]],
    playlist_priority: bool = True,
    title_priority: bool = True,
    description_priority: bool = True,
    default_category: str | None = None,
) -> ClassificationResult:
    """Pure function — deterministic and unit-testable."""
    title = (video.get("title") or "").lower()
    description = (video.get("description") or "").lower()
    playlist_title = (video.get("playlist_title") or "").lower()

    def match_rules_on(text: str) -> tuple[str, int] | None:
        if not text:
            return None
        for index, rule in enumerate(rules):
            for needle in rule["match"]:
                if needle and needle in text:
                    return rule["category"], index
        return None

    # 1) explicit rules with configured field priority
    fields: list[tuple[str, str, bool]] = []
    if playlist_priority:
        fields.append(("playlist", playlist_title, True))
    if title_priority:
        fields.append(("title", title, True))
    if description_priority:
        fields.append(("description", description, True))
    # stable order when all priorities true: playlist → title → description
    for source_name, text, _enabled in fields:
        hit = match_rules_on(text)
        if hit:
            category, index = hit
            return ClassificationResult(
                category=category, matched_by=source_name, rule_index=index
            )

    # 2) default category from content config (still deterministic)
    if default_category:
        return ClassificationResult(
            category=default_category, matched_by="default"
        )

    return ClassificationResult(category=UNCLASSIFIED, matched_by="unclassified")


def ensure_category(conn, ustaz_id: str, name: str) -> int:
    slug = slugify(name)
    row = conn.execute(
        "SELECT id FROM category WHERE ustaz_id = ? AND slug = ?",
        (ustaz_id, slug),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO category (ustaz_id, slug, name) VALUES (?, ?, ?)",
        (ustaz_id, slug, name),
    )
    return int(cur.lastrowid or 0)


class Classifier:
    def __init__(self) -> None:
        pass

    def run(
        self,
        ustaz_id: str,
        *,
        dry_run: bool = False,
        reclassify: bool = False,
        db_path=None,
        config: dict[str, Any] | None = None,
    ) -> ClassifyReport:
        report = ClassifyReport(ustaz_id=ustaz_id, dry_run=dry_run)

        if config is None:
            try:
                merged = load_ustaz_config(ustaz_id)
            except ConfigError as exc:
                raise ConfigError(str(exc)) from exc
            ustaz_cfg = merged["ustaz"]
            universal = merged["universal"]
        else:
            ustaz_cfg = config.get("ustaz") or config
            universal = config.get("universal") or {}

        class_cfg = ustaz_cfg.get("classification") or {}
        if not class_cfg.get("enabled", True):
            report.errors.append("classification disabled in config")
            return report

        rules = _normalize_rules(class_cfg.get("match_rules"))
        # legacy: rules dict may embed list under classification.rules if shaped that way
        if not rules and isinstance(class_cfg.get("rules"), list):
            rules = _normalize_rules(class_cfg["rules"])

        rule_flags = class_cfg.get("rules") or {}
        if not isinstance(rule_flags, dict):
            rule_flags = {}
        playlist_priority = bool(rule_flags.get("playlist_priority", True))
        title_priority = bool(rule_flags.get("title_priority", True))
        description_priority = bool(rule_flags.get("description_priority", True))
        default_category = ((ustaz_cfg.get("content") or {}).get("default_category"))

        conn = init_db(db_path)
        try:
            if reclassify:
                rows = conn.execute(
                    """
                    SELECT * FROM video
                    WHERE ustaz_id = ?
                      AND state NOT IN ('DISCOVERED', 'METADATA_READY')
                    """,
                    (ustaz_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM video
                    WHERE ustaz_id = ?
                      AND classification_status = 'PENDING'
                      AND state NOT IN ('DISCOVERED', 'METADATA_READY', 'FAILED')
                    """,
                    (ustaz_id,),
                ).fetchall()

            report.scanned = len(rows)
            for row in rows:
                video = dict(row)
                try:
                    result = classify_video(
                        video,
                        rules=rules,
                        playlist_priority=playlist_priority,
                        title_priority=title_priority,
                        description_priority=description_priority,
                        default_category=default_category,
                    )
                except Exception as exc:  # noqa: BLE001 - failure isolation
                    report.errors.append(f"{video.get('video_id')}: {exc}")
                    log_event(
                        "classification",
                        f"classify error: {exc}",
                        level=30,
                        ustaz_id=ustaz_id,
                        video_id=str(video.get("video_id")),
                    )
                    if not dry_run:
                        conn.execute(
                            """
                            UPDATE video
                            SET classification_status = 'FAILED', last_error = ?
                            WHERE ustaz_id = ? AND video_id = ?
                            """,
                            (str(exc), ustaz_id, video["video_id"]),
                        )
                    continue

                report.already_done += 0
                if result.category == UNCLASSIFIED:
                    report.unclassified += 1
                else:
                    report.classified += 1

                if dry_run:
                    continue

                category_id = ensure_category(conn, ustaz_id, result.category)
                status = (
                    "UNCLASSIFIED"
                    if result.category == UNCLASSIFIED
                    else "CLASSIFIED"
                )
                prev = video.get("category")
                conn.execute(
                    """
                    UPDATE video
                    SET category = ?,
                        classification_status = ?,
                        state = CASE
                            WHEN state IN ('DOWNLOADED', 'CLASSIFIED')
                            THEN 'CLASSIFIED'
                            WHEN state = 'GROUPED' THEN 'GROUPED'
                            WHEN state = 'INDEXED' THEN 'INDEXED'
                            WHEN state = 'PUBLISHED' THEN 'PUBLISHED'
                            ELSE state
                        END,
                        last_error = NULL
                    WHERE ustaz_id = ? AND video_id = ?
                    """,
                    (result.category, status, ustaz_id, video["video_id"]),
                )
                # replace join row
                conn.execute(
                    "DELETE FROM video_category WHERE ustaz_id = ? AND video_id = ?",
                    (ustaz_id, video["video_id"]),
                )
                conn.execute(
                    """
                    INSERT INTO video_category (ustaz_id, video_id, category_id)
                    VALUES (?, ?, ?)
                    """,
                    (ustaz_id, video["video_id"], category_id),
                )
                if prev != result.category:
                    log_event(
                        "classification",
                        f"{result.category} via {result.matched_by}",
                        ustaz_id=ustaz_id,
                        video_id=str(video["video_id"]),
                    )

            if dry_run:
                conn.rollback()
            else:
                conn.commit()
            return report
        finally:
            conn.close()
