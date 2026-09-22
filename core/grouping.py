"""Grouping: source playlist → archive series (separate concepts).

YouTube playlist membership is preserved on video.playlist_id.
Archive series is the subject/series grouping (series table + video.series_id).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.classifier import slugify
from core.config import ConfigError, load_ustaz_config
from core.database import init_db
from core.logging_setup import log_event


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def series_id_for(ustaz_id: str, slug: str) -> str:
    """Stable deterministic series id."""
    digest = hashlib.sha256(f"{ustaz_id}:{slug}".encode("utf-8")).hexdigest()[:16]
    return f"ser_{digest}"


@dataclass
class GroupReport:
    ustaz_id: str
    dry_run: bool = False
    scanned: int = 0
    series_created: int = 0
    videos_grouped: int = 0
    videos_regrouped: int = 0
    videos_ungrouped: int = 0
    already_done: int = 0
    errors: list[str] = field(default_factory=list)


def _series_slug_from_playlist(playlist_title: str | None, playlist_id: str | None) -> tuple[str, str] | None:
    if playlist_title and playlist_title.strip():
        title = playlist_title.strip()
        return slugify(title), title
    if playlist_id:
        return slugify(playlist_id), playlist_id
    return None


def ensure_series(
    conn,
    *,
    ustaz_id: str,
    slug: str,
    title: str,
    description: str | None = None,
    category: str | None = None,
    language: str | None = None,
    dry_run: bool = False,
) -> tuple[str, bool]:
    """Return (series_id, created)."""
    existing = conn.execute(
        "SELECT series_id FROM series WHERE ustaz_id = ? AND slug = ?",
        (ustaz_id, slug),
    ).fetchone()
    if existing:
        return str(existing["series_id"]), False

    sid = series_id_for(ustaz_id, slug)
    if dry_run:
        return sid, True
    now = _utcnow()
    conn.execute(
        """
        INSERT INTO series (series_id, ustaz_id, slug, title, description,
                            category, language, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (ustaz_id, slug) DO NOTHING
        """,
        (sid, ustaz_id, slug, title, description, category, language, now, now),
    )
    return sid, True


class Grouper:
    def run(
        self,
        ustaz_id: str,
        *,
        dry_run: bool = False,
        db_path=None,
        config: dict[str, Any] | None = None,
        regroup: bool = False,
    ) -> GroupReport:
        """Group ungrouped videos into archive series.

        With regroup=True, every eligible video is re-resolved (topic rules
        first, so config changes re-catalog existing rows), moved videos get
        their stale series links removed, and series left with zero videos
        are deleted. Without regroup, only series_id IS NULL rows are touched.
        """
        report = GroupReport(ustaz_id=ustaz_id, dry_run=dry_run)

        if config is None:
            try:
                merged = load_ustaz_config(ustaz_id)
            except ConfigError as exc:
                raise ConfigError(str(exc)) from exc
            ustaz_cfg = merged["ustaz"]
        else:
            ustaz_cfg = config.get("ustaz") or config

        language = None
        langs = (ustaz_cfg.get("content") or {}).get("languages") or []
        if langs:
            language = str(langs[0])
        default_category = (ustaz_cfg.get("content") or {}).get("default_category")

        # optional explicit series rules: match title → series title
        class_cfg = ustaz_cfg.get("classification") or {}
        series_rules = class_cfg.get("series_rules") or []
        if not isinstance(series_rules, list):
            series_rules = []

        conn = init_db(db_path)
        try:
            if regroup:
                rows = conn.execute(
                    """
                    SELECT * FROM video
                    WHERE ustaz_id = ?
                      AND classification_status IN ('CLASSIFIED', 'UNCLASSIFIED', 'PENDING')
                      AND state NOT IN ('DISCOVERED', 'METADATA_READY')
                    """,
                    (ustaz_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM video
                    WHERE ustaz_id = ?
                      AND classification_status IN ('CLASSIFIED', 'UNCLASSIFIED', 'PENDING')
                      AND state NOT IN ('DISCOVERED', 'METADATA_READY')
                      AND (series_id IS NULL OR series_id = '')
                    """,
                    (ustaz_id,),
                ).fetchall()
            # also include videos already classified but need video_series refresh?
            # only ungrouped for now
            report.scanned = len(rows)

            for row in rows:
                video = dict(row)
                try:
                    title = (video.get("title") or "").strip()
                    playlist_title = video.get("playlist_title")
                    playlist_id = video.get("playlist_id")

                    # explicit series rules first (title match)
                    slug = None
                    series_title = None
                    for rule in series_rules:
                        if not isinstance(rule, dict):
                            continue
                        needles = rule.get("match") or []
                        series_title = rule.get("series") or rule.get("title")
                        if series_title and any(
                            str(n).lower() in title.lower()
                            for n in needles
                            if str(n).strip()
                        ):
                            slug = slugify(str(series_title))
                            break

                    if slug is None:
                        from_playlist = _series_slug_from_playlist(
                            playlist_title, playlist_id
                        )
                        if from_playlist:
                            slug, series_title = from_playlist
                        else:
                            # no playlist and no rule → leave ungrouped (ok)
                            report.videos_ungrouped += 1
                            continue

                    previous_series = video.get("series_id") or ""

                    if not series_title:
                        series_title = slug

                    sid, created = ensure_series(
                        conn,
                        ustaz_id=ustaz_id,
                        slug=slug,
                        title=series_title,
                        description=None,
                        category=video.get("category") or default_category,
                        language=language,
                        dry_run=dry_run,
                    )
                    if created:
                        report.series_created += 1

                    if dry_run:
                        report.videos_grouped += 1
                        if regroup and previous_series and previous_series != sid:
                            report.videos_regrouped += 1
                        continue

                    conn.execute(
                        """
                        UPDATE video
                        SET series_id = ?,
                            state = CASE
                                WHEN state IN ('DOWNLOADED', 'CLASSIFIED', 'GROUPED')
                                THEN 'GROUPED'
                                WHEN state = 'INDEXED' THEN 'INDEXED'
                                WHEN state = 'PUBLISHED' THEN 'PUBLISHED'
                                ELSE state
                            END
                        WHERE ustaz_id = ? AND video_id = ?
                        """,
                        (sid, ustaz_id, video["video_id"]),
                    )
                    conn.execute(
                        """
                        INSERT INTO video_series (ustaz_id, video_id, series_id, position)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT (ustaz_id, video_id, series_id) DO UPDATE SET
                            position = excluded.position
                        """,
                        (
                            ustaz_id,
                            video["video_id"],
                            sid,
                            video.get("playlist_position"),
                        ),
                    )
                    # one archive series per video — drop links to old series
                    conn.execute(
                        """
                        DELETE FROM video_series
                        WHERE ustaz_id = ? AND video_id = ? AND series_id != ?
                        """,
                        (ustaz_id, video["video_id"], sid),
                    )
                    report.videos_grouped += 1
                    if regroup and previous_series and previous_series != sid:
                        report.videos_regrouped += 1
                except Exception as exc:  # noqa: BLE001 - isolation
                    report.errors.append(
                        f"{video.get('video_id')}: {exc}"
                    )
                    log_event(
                        "group",
                        f"group error: {exc}",
                        level=30,
                        ustaz_id=ustaz_id,
                        video_id=str(video.get("video_id")),
                    )
                    continue

            if regroup and not dry_run:
                # drop series left with zero videos after moves
                conn.execute(
                    """
                    DELETE FROM series
                    WHERE ustaz_id = ?
                      AND series_id NOT IN (
                          SELECT series_id FROM video
                          WHERE ustaz_id = ? AND series_id IS NOT NULL
                            AND series_id != ''
                      )
                    """,
                    (ustaz_id, ustaz_id),
                )
                log_event(
                    "group",
                    f"regrouped {report.videos_regrouped} videos, "
                    f"{report.series_created} series created",
                    ustaz_id=ustaz_id,
                )

            if dry_run:
                conn.rollback()
            else:
                conn.commit()
            return report
        finally:
            conn.close()
