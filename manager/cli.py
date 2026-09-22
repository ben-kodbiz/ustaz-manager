"""Archive Manager CLI. Phases 1–7: doctor/list/status/dashboard/create/add/discover/download/classify/group/build/publish."""

from __future__ import annotations

import argparse
import sys

from core import paths
from core.config import ConfigError, load_all, load_registry, validate_all
from core.logging_setup import get_logger, log_event
from manager import doctor as doctor_mod
from manager import registry as registry_mod

# stage → phase that implements it (todoagent.md §60)
STAGE_PHASES = {
    "build": 5,
    "publish": 6,
    "run": 5,
}
# Phases with real implementations (still validated before run)
IMPLEMENTED_STAGES = frozenset(
    {
        "discover",
        "sync",
        "download",
        "resume",
        "retry-failed",
        "classify",
        "group",
        "build",
        "publish",
    }
)


def _print_config_problems(problems: list[str]) -> None:
    for problem in problems:
        print(problem, file=sys.stderr)


def cmd_doctor(_args: argparse.Namespace) -> int:
    checks, ok = doctor_mod.run_doctor()
    print(doctor_mod.format_report(checks, ok))
    return 0 if ok else 1


def cmd_list(_args: argparse.Namespace) -> int:
    try:
        entries = load_registry()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not entries:
        print("No Ustaz registered. Use: ./archive.zsh create <id>")
        return 0
    width = max(len(e.id) for e in entries)
    print(f"{'ID':<{width}}  {'ENABLED':<7}  NAME  OUTPUT")
    for e in entries:
        flag = "yes" if e.enabled else "no"
        print(f"{e.id:<{width}}  {flag:<7}  {e.name}  {e.output}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    problems = validate_all()
    if problems:
        _print_config_problems(problems)
        return 1
    from core import database

    try:
        conn = database.init_db()
    except Exception as exc:  # noqa: BLE001
        print(f"database error: {exc}", file=sys.stderr)
        return 1

    try:
        try:
            entries = load_registry()
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        targets = entries
        if args.ustaz_id:
            targets = [e for e in entries if e.id == args.ustaz_id]
            if not targets:
                print(f"unknown ustaz id: {args.ustaz_id}", file=sys.stderr)
                return 1
        print(f"{'USTAZ':<16}  {'VIDEOS':>7}  {'PENDING':>7}  {'FAILED':>7}  {'DOWNLOADED':>10}")
        for entry in targets:
            total = conn.execute(
                "SELECT count(*) FROM video WHERE ustaz_id = ?", (entry.id,)
            ).fetchone()[0]
            new = conn.execute(
                "SELECT count(*) FROM video WHERE ustaz_id = ? "
                "AND download_status = 'PENDING'",
                (entry.id,),
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT count(*) FROM video WHERE ustaz_id = ? AND download_status = 'FAILED'",
                (entry.id,),
            ).fetchone()[0]
            downloaded = conn.execute(
                "SELECT count(*) FROM video WHERE ustaz_id = ? AND download_status = 'COMPLETED'",
                (entry.id,),
            ).fetchone()[0]
            print(
                f"{entry.id:<16}  {total:>7}  {new:>7}  {failed:>7}  {downloaded:>10}"
            )
        return 0
    finally:
        conn.close()


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Phase 7: read-only local dashboard from SQLite (never writes)."""
    from manager import dashboard as dashboard_mod

    problems = validate_all()
    if problems:
        _print_config_problems(problems)
        return 1
    from core import database

    try:
        conn = database.init_db()
    except Exception as exc:  # noqa: BLE001
        print(f"database error: {exc}", file=sys.stderr)
        return 1

    try:
        try:
            entries = load_registry()
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.ustaz_id:
            entries = [e for e in entries if e.id == args.ustaz_id]
            if not entries:
                print(f"unknown ustaz id: {args.ustaz_id}", file=sys.stderr)
                return 1
        print(dashboard_mod.format_dashboard(dashboard_mod.collect_dashboard(conn, entries)))
        return 0
    finally:
        conn.close()


def cmd_create(args: argparse.Namespace) -> int:
    channels = args.channel or []
    try:
        entry = registry_mod.create_ustaz(
            args.ustaz_id,
            name=args.name,
            slug=args.slug,
            description=args.description,
            youtube_channels=channels,
            language=args.language,
            enabled=not args.disabled,
        )
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    log_event(
        "manager",
        f"created ustaz config and registered '{entry.id}'",
        ustaz_id=entry.id,
    )
    print(f"created {entry.config}")
    print(f"registered in {paths.REGISTRY_CONFIG.relative_to(paths.REPO_ROOT)}")
    print(f"output: {entry.output}/")
    print("next: edit sources if needed, then ./archive.zsh doctor")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    from pathlib import Path

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = paths.REPO_ROOT / config_path
    try:
        entry = registry_mod.register_existing(config_path, enabled=not args.disabled)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"registered {entry.id} → {entry.config}")
    return 0


def _configured_targets(args: argparse.Namespace) -> list[str] | int:
    """Return enabled ustaz ids for --all, or single id; int = exit code."""
    problems = validate_all()
    if problems:
        _print_config_problems(problems)
        return 1

    if getattr(args, "all", False):
        try:
            entries = [e for e in load_registry() if e.enabled]
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if not entries:
            print("no enabled Ustaz in registry", file=sys.stderr)
            return 1
        return [e.id for e in entries]

    if not getattr(args, "ustaz_id", None):
        print(
            "usage: archive.zsh <command> <ustaz_id>  (or --all)",
            file=sys.stderr,
        )
        return 2
    return [args.ustaz_id]


def cmd_discover(args: argparse.Namespace) -> int:
    from core.discovery import DiscoveryService

    targets = _configured_targets(args)
    if isinstance(targets, int):
        return targets

    spacing = 0.0 if getattr(args, "fast", False) else None  # None = config default
    # unit-like local runs still retry; backoff scaled down with --fast
    retry = 1 if getattr(args, "fast", False) else None
    backoff = 0.0 if getattr(args, "fast", False) else None

    service = DiscoveryService()
    exit_code = 0
    for ustaz_id in targets:
        try:
            report = service.discover(
                ustaz_id,
                dry_run=bool(getattr(args, "dry_run", False)),
                limit=getattr(args, "limit", None),
                spacing_seconds=spacing,
                retry_count=retry,
                backoff_seconds=backoff,
                prune=bool(getattr(args, "prune", False)),
            )
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001 - surface unexpected stage failures
            print(f"discovery failed for {ustaz_id}: {exc}", file=sys.stderr)
            log_event(
                "discovery", f"fatal: {exc}", level=40, ustaz_id=ustaz_id
            )
            exit_code = 1
            continue

        prefix = "DRY-RUN " if report.dry_run else ""
        print(
            f"{prefix}{ustaz_id}: sources={report.sources_ok}/{report.sources_total} "
            f"new={report.videos_new} updated={report.videos_updated} "
            f"unchanged={report.videos_unchanged} "
            f"skipped={report.videos_skipped} pruned={report.videos_pruned}"
        )
        for err in report.errors:
            print(f"  warning: {err}", file=sys.stderr)
        # partial source failures are non-fatal (recoverable); total failure is
        if report.sources_total > 0 and report.sources_ok == 0:
            exit_code = 1
    return exit_code


def cmd_download(args: argparse.Namespace) -> int:
    """download / resume / retry-failed share the Phase 3 queue runner."""
    from core.download_manager import DownloadManager
    from core.rate_limit import RateLimitConfig

    targets = _configured_targets(args)
    if isinstance(targets, int):
        return targets

    command = args.command
    retry_failed = command == "retry-failed"
    do_resume = command in ("download", "resume", "retry-failed")

    if getattr(args, "fast", False):
        service = DownloadManager(
            sleep=lambda _s: None,
            rate_limit=RateLimitConfig(
                source_spacing_seconds=0.0,
                source_spacing_jitter_seconds=0.0,
                cooldown_seconds=0.0,
                sleep_between_requests=0.0,
                max_sleep_between_requests=0.0,
            ),
        )
    else:
        service = DownloadManager()

    exit_code = 0
    for ustaz_id in targets:
        try:
            report = service.run(
                ustaz_id,
                dry_run=bool(getattr(args, "dry_run", False)),
                resume=do_resume,
                retry_failed=retry_failed,
                max_jobs=getattr(args, "limit", None),
            )
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"download failed for {ustaz_id}: {exc}", file=sys.stderr)
            log_event("download", f"fatal: {exc}", level=40, ustaz_id=ustaz_id)
            exit_code = 1
            continue

        prefix = "DRY-RUN " if report.dry_run else ""
        print(
            f"{prefix}{ustaz_id}: mode={report.mode} enqueued={report.enqueued} "
            f"completed={report.completed} skipped={report.skipped} "
            f"failed={report.failed} resumed={report.resumed} "
            f"already_done={report.already_done}"
        )
        for err in report.errors[:20]:
            print(f"  warning: {err}", file=sys.stderr)
        if report.failed and report.completed == 0 and report.skipped == 0:
            if not report.dry_run:
                exit_code = exit_code or 1
    return exit_code


def cmd_classify(args: argparse.Namespace) -> int:
    from core.classifier import Classifier

    targets = _configured_targets(args)
    if isinstance(targets, int):
        return targets

    service = Classifier()
    exit_code = 0
    for ustaz_id in targets:
        try:
            report = service.run(
                ustaz_id,
                dry_run=bool(getattr(args, "dry_run", False)),
                reclassify=bool(getattr(args, "reclassify", False)),
            )
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"classify failed for {ustaz_id}: {exc}", file=sys.stderr)
            log_event("classification", f"fatal: {exc}", level=40, ustaz_id=ustaz_id)
            exit_code = 1
            continue

        prefix = "DRY-RUN " if report.dry_run else ""
        print(
            f"{prefix}{ustaz_id}: scanned={report.scanned} "
            f"classified={report.classified} unclassified={report.unclassified} "
            f"already={report.already_done}"
        )
        for err in report.errors[:20]:
            print(f"  warning: {err}", file=sys.stderr)
    return exit_code


def cmd_group(args: argparse.Namespace) -> int:
    from core.grouping import Grouper

    targets = _configured_targets(args)
    if isinstance(targets, int):
        return targets

    service = Grouper()
    exit_code = 0
    for ustaz_id in targets:
        try:
            report = service.run(
                ustaz_id,
                dry_run=bool(getattr(args, "dry_run", False)),
                regroup=bool(getattr(args, "regroup", False)),
            )
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"group failed for {ustaz_id}: {exc}", file=sys.stderr)
            log_event("group", f"fatal: {exc}", level=40, ustaz_id=ustaz_id)
            exit_code = 1
            continue

        prefix = "DRY-RUN " if report.dry_run else ""
        print(
            f"{prefix}{ustaz_id}: scanned={report.scanned} "
            f"series_created={report.series_created} "
            f"videos_grouped={report.videos_grouped} "
            f"regrouped={report.videos_regrouped} "
            f"ungrouped={report.videos_ungrouped} "
            f"already={report.already_done}"
        )
        for err in report.errors[:20]:
            print(f"  warning: {err}", file=sys.stderr)
    return exit_code


def cmd_build(args: argparse.Namespace) -> int:
    from core.sitegen import SiteBuilder

    targets = _configured_targets(args)
    if isinstance(targets, int):
        return targets

    service = SiteBuilder()
    exit_code = 0
    for ustaz_id in targets:
        try:
            report = service.run(
                ustaz_id,
                dry_run=bool(getattr(args, "dry_run", False)),
            )
        except ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"build failed for {ustaz_id}: {exc}", file=sys.stderr)
            log_event("build", f"fatal: {exc}", level=40, ustaz_id=ustaz_id)
            exit_code = 1
            continue

        prefix = "DRY-RUN " if report.dry_run else ""
        print(
            f"{prefix}{ustaz_id}: pages={report.pages} videos={report.videos} "
            f"categories={report.categories} series={report.series} "
            f"playlists={report.playlists} stale_removed={report.stale_removed} "
            f"output={report.output}"
        )
        for err in report.errors[:20]:
            print(f"  warning: {err}", file=sys.stderr)
        if report.errors and report.pages == 0 and not report.dry_run:
            exit_code = exit_code or 1
    return exit_code


def cmd_publish(args: argparse.Namespace) -> int:
    """Phase 6: rebuild the static site, validate it, report deploy readiness.

    Does not push or deploy itself — deploy flow is:
    publish (build + validate) → git commit → Actions → Pages.
    With --dry-run, only validate the existing apps/<id>/ output (no rebuild).
    """
    from core.siteval import format_report as format_site_report
    from core.siteval import validate_ustaz_output

    targets = _configured_targets(args)
    if isinstance(targets, int):
        return targets

    try:
        entries = {e.id: e for e in load_registry()}
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    dry_run = bool(getattr(args, "dry_run", False))
    exit_code = 0
    for ustaz_id in targets:
        entry = entries.get(ustaz_id)
        if entry is None:
            print(f"unknown ustaz id: {ustaz_id}", file=sys.stderr)
            return 1
        output_dir = paths.REPO_ROOT / entry.output

        if not dry_run:
            from core.sitegen import SiteBuilder

            try:
                build_report = SiteBuilder().run(ustaz_id)
            except ConfigError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            except Exception as exc:  # noqa: BLE001
                print(f"publish build failed for {ustaz_id}: {exc}", file=sys.stderr)
                log_event("publish", f"fatal: {exc}", level=40, ustaz_id=ustaz_id)
                exit_code = 1
                continue
            if build_report.errors and build_report.pages == 0:
                for err in build_report.errors[:20]:
                    print(f"  warning: {err}", file=sys.stderr)
                exit_code = exit_code or 1
                continue

        site_report = validate_ustaz_output(ustaz_id, output_dir=output_dir)
        print(format_site_report(site_report))
        log_event(
            "publish",
            f"validated {site_report.checked_files} files, "
            f"{site_report.checked_links} links ok={site_report.ok}",
            ustaz_id=ustaz_id,
        )
        if not site_report.ok:
            exit_code = 1
            continue
        print(
            f"{ustaz_id}: publish-ready → {output_dir} "
            "(next: git add, commit, push → Actions → Pages)"
        )
    return exit_code


def _stub_stage(stage: str, args: argparse.Namespace) -> int:
    problems = validate_all()
    if problems:
        _print_config_problems(problems)
        return 1

    targets = _configured_targets(args)
    if isinstance(targets, int):
        return targets

    phase = STAGE_PHASES.get(stage, 2)
    for ustaz_id in targets:
        print(
            f"[phase {phase}] '{stage}' for '{ustaz_id}' not implemented yet "
            f"(config OK — run ./archive.zsh doctor)"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="archive.zsh",
        description="Ustaz Archive Manager — configuration-driven archive factory",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="validate environment, configs, and database")

    sub.add_parser("list", help="list registered Ustaz entries")

    p_status = sub.add_parser("status", help="per-Ustaz video counts from SQLite")
    p_status.add_argument("ustaz_id", nargs="?", help="limit to one Ustaz")

    p_dashboard = sub.add_parser(
        "dashboard", help="local operator dashboard from SQLite (Phase 7)"
    )
    p_dashboard.add_argument("ustaz_id", nargs="?", help="limit to one Ustaz")

    p_create = sub.add_parser("create", help="scaffold config and register a new Ustaz")
    p_create.add_argument("ustaz_id")
    p_create.add_argument("--name", help="display name")
    p_create.add_argument("--slug", help="URL slug")
    p_create.add_argument(
        "--description", default="Islamic lectures and educational talks"
    )
    p_create.add_argument(
        "--channel",
        action="append",
        help="YouTube channel URL (repeatable)",
    )
    p_create.add_argument("--language", default="ms")
    p_create.add_argument(
        "--disabled", action="store_true", help="register as disabled"
    )

    p_add = sub.add_parser("add", help="register an existing ustaz config file")
    p_add.add_argument("config", help="path to config/ustaz/<id>.json")
    p_add.add_argument(
        "--disabled", action="store_true", help="register as disabled"
    )

    for stage in sorted(STAGE_PHASES):
        p_stage = sub.add_parser(stage, help=f"{stage} (Phase {STAGE_PHASES[stage]})")
        p_stage.add_argument(
            "ustaz_id", nargs="?", help="target Ustaz id (omit with --all)"
        )
        p_stage.add_argument(
            "--all", action="store_true", help="run for every enabled Ustaz"
        )
        p_stage.add_argument(
            "--dry-run",
            action="store_true",
            help="report planned work only (no changes)",
        )

    for stage in ("discover", "sync"):
        p_stage = sub.add_parser(
            stage,
            help=f"{stage} — YouTube metadata discovery (Phase 2)",
        )
        p_stage.add_argument(
            "ustaz_id", nargs="?", help="target Ustaz id (omit with --all)"
        )
        p_stage.add_argument(
            "--all", action="store_true", help="run for every enabled Ustaz"
        )
        p_stage.add_argument(
            "--dry-run",
            action="store_true",
            help="list what would change without writing DB/files",
        )
        p_stage.add_argument(
            "--limit",
            type=int,
            default=None,
            help="max entries per source (yt-dlp --playlist-end)",
        )
        p_stage.add_argument(
            "--fast",
            action="store_true",
            help="no inter-source spacing / minimal retries (local testing)",
        )
        p_stage.add_argument(
            "--prune",
            action="store_true",
            help="delete stored videos failing content.speaker_names "
            "(explicit reconciliation; refused when unset)",
        )

    for stage in ("download", "resume", "retry-failed"):
        p_stage = sub.add_parser(
            stage,
            help=f"{stage} — download queue (Phase 3)",
        )
        p_stage.add_argument(
            "ustaz_id", nargs="?", help="target Ustaz id (omit with --all)"
        )
        p_stage.add_argument(
            "--all", action="store_true", help="run for every enabled Ustaz"
        )
        p_stage.add_argument(
            "--dry-run",
            action="store_true",
            help="report queue counts only (no downloads)",
        )
        p_stage.add_argument(
            "--limit",
            type=int,
            default=None,
            help="max jobs to process this run",
        )
        p_stage.add_argument(
            "--fast",
            action="store_true",
            help="no pacing sleeps (local testing)",
        )

    for stage in ("classify", "group"):
        p_stage = sub.add_parser(
            stage,
            help=f"{stage} — classification / grouping (Phase 4)",
        )
        p_stage.add_argument(
            "ustaz_id", nargs="?", help="target Ustaz id (omit with --all)"
        )
        p_stage.add_argument(
            "--all", action="store_true", help="run for every enabled Ustaz"
        )
        p_stage.add_argument(
            "--dry-run",
            action="store_true",
            help="report what would change (no writes)",
        )
        if stage == "classify":
            p_stage.add_argument(
                "--reclassify",
                action="store_true",
                help="re-run classification on already classified videos",
            )
        if stage == "group":
            p_stage.add_argument(
                "--regroup",
                action="store_true",
                help="re-resolve series for all videos (topic rules first), "
                "move videos, drop empty series",
            )

    return parser


def main(argv: list[str] | None = None) -> int:
    paths.ensure_standard_dirs()
    get_logger("manager")
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "doctor": cmd_doctor,
        "list": cmd_list,
        "status": cmd_status,
        "dashboard": cmd_dashboard,
        "create": cmd_create,
        "add": cmd_add,
        "discover": cmd_discover,
        "sync": cmd_discover,
        "download": cmd_download,
        "resume": cmd_download,
        "retry-failed": cmd_download,
        "classify": cmd_classify,
        "group": cmd_group,
        "build": cmd_build,
        "publish": cmd_publish,
    }
    handler = handlers.get(args.command)
    if handler is not None:
        return handler(args)
    if args.command in STAGE_PHASES:
        return _stub_stage(args.command, args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
