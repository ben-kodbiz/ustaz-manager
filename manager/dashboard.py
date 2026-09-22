"""Local manager dashboard (Phase 7). Read-only reporting over SQLite.

This is the operator view — it is NOT the public Ustaz website.
All state comes from the database (source of truth); nothing is inferred
from directories and nothing is written.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DashboardRow:
    ustaz_id: str
    name: str
    enabled: bool
    videos: int = 0
    pending: int = 0
    failed: int = 0
    downloaded: int = 0
    series: int = 0
    last_sync: str = "-"
    build_status: str = "-"
    build_at: str = "-"
    build_videos: int = 0


@dataclass
class Dashboard:
    rows: list[DashboardRow] = field(default_factory=list)
    recent_errors: list[dict] = field(default_factory=list)


def _one(conn, query: str, params: tuple = ()) -> object:
    row = conn.execute(query, params).fetchone()
    return row[0] if row is not None else None


def collect_dashboard(conn, entries) -> Dashboard:
    """Collect one row per registry entry plus recent errors. Read-only."""
    dashboard = Dashboard()
    for entry in entries:
        uid = entry.id
        videos = _one(
            conn, "SELECT count(*) FROM video WHERE ustaz_id = ?", (uid,)
        ) or 0
        pending = _one(
            conn,
            "SELECT count(*) FROM video WHERE ustaz_id = ? "
            "AND download_status = 'PENDING'",
            (uid,),
        ) or 0
        failed = _one(
            conn,
            "SELECT count(*) FROM video WHERE ustaz_id = ? "
            "AND (download_status = 'FAILED' OR state = 'FAILED')",
            (uid,),
        ) or 0
        downloaded = _one(
            conn,
            "SELECT count(*) FROM video WHERE ustaz_id = ? "
            "AND download_status = 'COMPLETED'",
            (uid,),
        ) or 0
        series = _one(
            conn, "SELECT count(*) FROM series WHERE ustaz_id = ?", (uid,)
        ) or 0
        last_sync = (
            _one(
                conn,
                "SELECT max(last_seen_at) FROM video WHERE ustaz_id = ?",
                (uid,),
            )
            or "-"
        )
        build = conn.execute(
            "SELECT status, finished_at, video_count FROM site_build "
            "WHERE ustaz_id = ? ORDER BY id DESC LIMIT 1",
            (uid,),
        ).fetchone()
        dashboard.rows.append(
            DashboardRow(
                ustaz_id=uid,
                name=entry.name,
                enabled=entry.enabled,
                videos=int(videos),
                pending=int(pending),
                failed=int(failed),
                downloaded=int(downloaded),
                series=int(series),
                last_sync=str(last_sync),
                build_status=str(build["status"]) if build else "-",
                build_at=str(build["finished_at"] or "-") if build else "-",
                build_videos=int(build["video_count"] or 0) if build else 0,
            )
        )

    ids = [e.id for e in entries]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        error_rows = conn.execute(
            "SELECT ustaz_id, stage, message, created_at FROM error_log "
            f"WHERE ustaz_id IN ({placeholders}) "
            "ORDER BY id DESC LIMIT 5",
            tuple(ids),
        ).fetchall()
        dashboard.recent_errors = [dict(r) for r in error_rows]
    return dashboard


def format_dashboard(dashboard: Dashboard) -> str:
    lines = ["Archive Manager", "─" * 40]
    header = (
        f"{'USTAZ':<16}  {'VIDEOS':>7}  {'PENDING':>7}  {'FAILED':>7}  "
        f"{'DOWNLOADED':>10}  {'SERIES':>6}  {'LAST SYNC':<20}  BUILD"
    )
    lines.append(header)
    for r in dashboard.rows:
        flag = "" if r.enabled else " (disabled)"
        lines.append(
            f"{r.ustaz_id:<16}  {r.videos:>7}  {r.pending:>7}  "
            f"{r.failed:>7}  {r.downloaded:>10}  {r.series:>6}  "
            f"{r.last_sync:<20}  {r.build_status}{flag}"
        )
    lines.append("─" * 40)
    if dashboard.recent_errors:
        lines.append("Recent failures:")
        for err in dashboard.recent_errors:
            lines.append(
                f"  [{err.get('created_at')}] {err.get('ustaz_id')}/"
                f"{err.get('stage')}: {err.get('message')}"
            )
    else:
        lines.append("Recent failures: none")
    return "\n".join(lines)
