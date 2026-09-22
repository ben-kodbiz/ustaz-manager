# AGENTS.md

## What this repo is

Ustaz Archive Factory: one shared Python pipeline builds many config-driven Ustaz archive static sites.

- Spec (requirements): `todoagent.md` — read before large changes.
- Design (authoritative architecture): `ARCHITECTURE.md` — keep in sync with code.
- Phase status lives in `ARCHITECTURE.md` § Development phases. Do not skip phases.

## Commands

```zsh
python3 -m unittest discover -s tests -v   # tests (stdlib unittest, no pytest)
./archive.zsh doctor                       # must pass after any change
./archive.zsh list
./archive.zsh status
```

Verification order: **tests → doctor**. No npm, no pip deps for Phase 1; Python 3.10+ stdlib only.

## Architecture rules (non-negotiable)

- Core (`core/`, `manager/`) must stay Ustaz-agnostic. Never `if ustaz_id == ...` in pipeline code — use `config/ustaz/<id>.json` + `config/ustaz_registry.json`.
- Adding a Ustaz = `./archive.zsh create <id>` + edit config. Zero core-code changes.
- Video identity = `(ustaz_id, video_id)` primary key. Never title/filename.
- SQLite (`data/database/archive.db`) is source of truth for state. Never infer state from directories.
- Discovery (metadata) and download are separate stages. Never redownload completed videos; never silently delete `data/`.
- Config validated against `schemas/*.schema.json` before any stage runs; fail early with `ERROR:\n<file>\n\n<detail>` shape.
- Frontend: vanilla HTML/CSS/JS only (Pico allowed). No React/Vue/Next/Tailwind/heavy frameworks.
- LLM classification optional; pipeline must work without AI.
- No CAPTCHA/anti-bot bypass; yt-dlp used conservatively (spacing, retries, single worker default).
- Rate pacing only via `core/rate_limit.py` + yt-dlp `--sleep-requests` — **never** add UA spoofing, fingerprint spoofing, stealth automation, or proxy rotation to evade limits (spec §26/§55/§62).
- Stdlib first (`sqlite3`, `argparse`, `json`, `unittest`). zsh for shell entrypoints (`archive.zsh`, `scripts/run.zsh`).

## Layout

- `core/` — paths, validation, config, database, logging, discovery (`core/sources/` adapters)
- `manager/` — CLI (`python3 -m manager`), registry, doctor
- `config/` — universal, registry, per-ustaz JSON (+ optional `*.local.json` overrides, gitignored)
- `schemas/` — JSON schemas (subset keywords handled by `core/validation.py`)
- `templates/` — ustaz config template + site template (`templates/site/default/`)
- `apps/<id>/` — generated sites only; `data/raw/`, `data/database/*.db`, `logs/` are gitignored

## Phase 2–5 specifics

- `discover`/`sync`, `download`/`resume`/`retry-failed`, `classify`/`group`, `build`, and `publish` are real; `run` is a config-validated stub.
- yt-dlp runs as a **binary** via `subprocess` argv lists in `core/sources/youtube.py` and `core/downloader/ytdlp.py` — never build shell strings from video titles/URLs.
- Unit tests must inject a fake `SourceAdapter` / `Downloader` or pass `config=` into Classifier/Grouper; no network in `tests/`.
- Discovery must not mutate `download_status` / `download_path` (download stage owns them).
- Download queue: one attempt per video per run; `COMPLETED`/`SKIPPED` never re-queued; interrupted `DOWNLOADING` reclaimed on resume.
- Classification is rules-only and pure (`classify_video`); source `playlist_id` ≠ archive `series_id` — never collapse them.
- LLM classification must never be required; failure → per-video `FAILED`/`Unclassified`, never abort the run.
- Site build (`core/sitegen.py`) is stdlib-only: no template frameworks; vanilla HTML/CSS/JS; optional `site.base_url` for absolute URLs.

## Workflow

1. Read `ARCHITECTURE.md` and inspect existing modules before changing code.
2. Tests with any significant change; run tests + `doctor` before finishing.
3. Update `ARCHITECTURE.md` when boundaries/schema/commands change.
4. Never rewrite working components needlessly; report what changed and what was tested.
