# ARCHITECTURE — Ustaz Archive Factory

Authoritative design derived from `todoagent.md`. Code and config in this repo must match this document; update it when architecture changes.

## Goals

- One shared pipeline ("Archive Manager") builds many independent Ustaz archive sites.
- Adding a Ustaz = add config + register it. Never `if ustaz == "..."` in core code.
- Static HTML/CSS/vanilla-JS sites deployable to GitHub Pages. Python stdlib + SQLite for the pipeline. No heavy frontend frameworks, no Tailwind, no mandatory LLM.

## Repository structure (Phase 1)

```text
ustazbrain/
├── archive.zsh              # CLI entrypoint → python3 -m manager
├── README.md
├── LICENSE
├── AGENTS.md
├── ARCHITECTURE.md          # this file
├── todoagent.md             # source specification
├── config/
│   ├── universal.json       # global behavior (no Ustaz-specific data)
│   ├── ustaz_registry.json  # which Ustaz apps exist / enabled
│   └── ustaz/
│       ├── ustaz_a.json
│       └── <ustaz-id>.json
├── schemas/
│   ├── universal.schema.json
│   ├── ustaz.schema.json
│   └── registry.schema.json
├── core/                    # shared pipeline (no Ustaz-specific logic)
│   ├── paths.py             # repo root + standard directories
│   ├── validation.py        # minimal JSON-Schema-subset validator (stdlib)
│   ├── config.py            # load + inherit + validate configs
│   ├── database.py          # SQLite schema init / connection
│   └── logging_setup.py     # structured logs under logs/
├── manager/                 # orchestration + CLI
│   ├── __main__.py          # python3 -m manager
│   ├── cli.py               # argparse commands
│   ├── registry.py          # registry load/list/create
│   └── doctor.py            # environment + config health checks
├── templates/
│   ├── config/ustaz.json    # template for `create`
│   └── site/default/        # Phase 5 site template
├── scripts/run.zsh          # thin alias for ./archive.zsh run
├── tests/                   # python3 -m unittest discover -s tests -v
├── apps/<ustaz-id>/         # generated static sites (Mode A monorepo)
├── data/
│   ├── database/archive.db  # SQLite source of truth (gitignored)
│   ├── raw/<ustaz>/...      # downloads (gitignored)
│   ├── normalized/<ustaz>/  # normalized JSON exports
│   └── generated/<ustaz>/   # intermediate build artifacts
├── logs/                    # structured logs (gitignored)
└── reports/                 # status reports (Phase: reports)
```

## Module boundaries

| Area | Owns | Must not contain |
|------|------|------------------|
| `core/` | Config load/validate, SQLite, logging, shared state helpers | Ustaz names, source URLs, persona text |
| `core/` pipeline stages (later: discovery, downloader, classifier, generator, publisher) | Stage logic behind stable interfaces | Hard-coded Ustaz IDs |
| `manager/` | Orchestration, CLI, registry, doctor | Stage internals |
| `config/` | Identity + sources + persona + schedule per Ustaz | Global runtime knobs (those live in universal.json) |
| `templates/` | Reusable output shapes | Live data |
| `apps/` | Generated site output only | Business logic |
| `data/` | DB, raw media, normalized/generated data | Code |

Plugin seams (Phase 2+): `SourceAdapter`, `Downloader`, `MetadataNormalizer`, `Classifier`, `SeriesResolver`, `SiteGenerator`, `Publisher` — one module per implementation (e.g. `core/sources/youtube.py`).

## Configuration model

Inheritance (deep-merge, later wins):

```text
config/universal.json
        ↓
config/ustaz/<id>.json
        ↓
config/ustaz/<id>.local.json   (optional, gitignored)
```

- **universal.json** — project, runtime, storage paths, youtube defaults, processing, classification defaults, site defaults, publishing. No Ustaz-specific fields.
- **ustaz_registry.json** — list of `{id, name, enabled, config, output}`. Manager iterates `enabled` entries only.
- **ustaz/<id>.json** — identity, sources (channels/playlists), content, classification rules, site, persona, schedule.
- All three validated against `schemas/*.schema.json` **before** any pipeline stage runs. Invalid config ⇒ fail early with a path-qualified error.

## Dependency graph (pipeline stages)

```text
DISCOVERY → METADATA → DOWNLOAD → VALIDATION → CLASSIFICATION → GROUPING
    → INDEX → SITE GENERATION → SITE VALIDATION → PUBLISH
```

Failure isolation rules:

- Failed download must not block metadata indexing.
- Failed classification must not destroy the site (bucket: `Unclassified`).
- Failed publish must not invalidate the local archive.
- Every stage is independently restartable (`resume` / re-run same command).

## Data flow

```text
universal config → registry → ustaz config → Manager
    → source discovery (yt-dlp, metadata-only)
    → raw metadata (data/raw/youtube/<video-id>.json)
    → SQLite (source of truth)
    → [download] [classify] [group]   (parallel-optional, default serial)
    → normalized JSON (data/normalized/<ustaz>/)
    → static generator (templates/site + data)
    → site validation
    → git commit → GitHub Actions → GitHub Pages
```

**Discovery ≠ download.** Discovery only records IDs/metadata. Download is a separate opt-in stage that consults DB state.

## Identity and deduplication

- Canonical video identity: `(ustaz_id, video_id)` — unique primary key. Never title, never filename.
- Canonical playlist membership: preserve `playlist_id`, `playlist_title`, `playlist_position`.
- **Source playlist** (YouTube) ≠ **archive series** (subject grouping). Separate tables; `series_id` on video is the subject link.

## Database (SQLite, `data/database/archive.db`)

Tables (see `core/database.py` for authoritative DDL):

- `ustaz`, `source`, `playlist`, `video`, `series`, `category`
- `video_series`, `video_category` — join tables
- `download_job`, `processing_job`, `jobs` — SQLite job queue (no Redis/Celery)
- `error_log`, `site_build`
- `video_fts` — FTS5 over title/description/playlist_title/series_title/category (created when FTS5 available)

SQLite is the source of truth for state. Never infer state from directory existence.

### Video state machine

```text
DISCOVERED → METADATA_READY → QUEUED → DOWNLOADING → DOWNLOADED
    → CLASSIFIED → GROUPED → INDEXED → PUBLISHED
                 ↘ FAILED → (retry) → …
```

Stored in `video.state`; download detail in `download_status` (`PENDING|DOWNLOADING|COMPLETED|FAILED|SKIPPED`); classification detail in `classification_status`.

## Classification (deterministic first)

Priority: manual rules → playlist metadata → title patterns → description → existing series rules → optional LLM → `Unclassified`.

- Rules are JSON, case-insensitive substring match → category (see ustaz config `classification.rules`).
- LLM is optional and must never be a hard dependency; classification failure never stops the archive.

## Site generation (Phase 5)

- One template tree: `templates/site/default/` → rendered into `apps/<id>/`.
- Pages auto-created from content: `/` (hero + one card per series + latest singles — no `/series` or `/playlists` index; the landing is the catalog), `/about`, `/search`, `/categories`, `/video/<id>`, `/series/<slug>`, `/playlist/<slug>`, `/category/<slug>`, plus `sitemap.xml`, `robots.txt`, per-page title/description/canonical/OpenGraph.
- Client loads `apps/<id>/data/*.json` with vanilla JS — no backend, no framework.
- Persona config controls branding/navigation/text only; pipeline stays identical across Ustazes.

## GitHub deployment model

- **Mode A (now):** monorepo — `apps/<id>/` committed here; GitHub Actions builds/validates and deploys Pages per app path.
- **Mode B (later):** split `ustaz-x-archive` repos when scale justifies it; factory still only emits static output.
- Never commit large media (`data/raw/`). Git holds code, config, metadata, generated sites.
- Deploy flow: factory build → validate → git commit → Actions → Pages. No permanent server required.

## CLI surface (`./archive.zsh`)

| Command | Phase | Behavior |
|---------|-------|----------|
| `doctor` | 1 | Validate env, schemas, configs, DB, directories |
| `list` | 1 | Show registry entries + enabled flag |
| `status` | 1 | Per-Ustaz counts from SQLite |
| `dashboard` | 7 | Read-only operator view: videos/pending/failed/downloaded, series, last sync, build status, recent failures |
| `create <id>` | 1 | Scaffold config from template + register |
| `add <id>` | 1 | Register an existing config file |
| `discover/download/classify/group/resume/retry-failed` | 2–4 | Implemented stages |
| `build` | 5 | Static site into `apps/<id>/` (pages, JSON, sitemap, robots, `.nojekyll`) |
| `publish` | 6 | Rebuild + validate `apps/<id>/` (deploy-ready; then git commit/push → Actions → Pages) |
| `sync` | 2 | Alias of `discover` |
| `run` | 5 | Config-validated stub until its phase lands |
| `--dry-run` on mutating stages | 3+ | Report planned work; change nothing |

Command order that matters: **validate config → stage work → tests**. Verification order: `python3 -m unittest discover -s tests -v` then `./archive.zsh doctor`.

## Development phases

1. **Foundation** (done): structure, configs, schemas, SQLite, validation, CLI, logging.
2. **Discovery** (done): yt-dlp channel/playlist flat extraction, dedup by `(ustaz_id, video_id)`, raw metadata under `data/raw/youtube/<id>.json`, playlist fields, retries + inter-source spacing. Commands: `./archive.zsh discover <id>` (alias: `sync`), flags `--dry-run --limit N --fast`.
3. **Download manager** (done): SQLite `download_job` queue (single worker), state machine `QUEUED → DOWNLOADING → DOWNLOADED|FAILED`, resume of interrupted jobs, one attempt/video/run + `retry-failed`, file validation (exists / size / video_id in name), never redownload `COMPLETED`. Modes: `metadata|audio|video` from per-ustaz `download.mode` (overrides universal youtube flags). Files: `data/raw/<id>/videos/<date>_<video_id>_<slug>.<ext>`. Commands: `download` / `resume` / `retry-failed` with `--dry-run --limit N --fast`.
4. **Classification + grouping** (done): deterministic rules only (`core/classifier.py`) — priority playlist → title → description → `content.default_category` → `Unclassified`; writes `category` / `video_category` join; `classification_status` ∈ `PENDING|CLASSIFIED|UNCLASSIFIED|FAILED`. Grouping (`core/grouping.py`): source playlist (`playlist_id`) ≠ archive series (`series.table` + `video.series_id` + `video_series`); optional `classification.series_rules` for title → series; failures isolated per video. Commands: `classify` (`--reclassify`), `group` (`--dry-run`).
5. **Static generator** (done): stdlib template engine (`core/sitegen.py` — `{{var}}` + `{{>partial}}`, `Raw` for unescaped HTML); pages `/`, `/about`, `/search`, `/categories`, `/playlists`, `/series`, `/video/<id>`, `/series/<slug>`, `/playlist/<slug>`, `/category/<slug>`; `sitemap.xml`, `robots.txt`; client JSON `data/search.json` + `data/videos.json`; optional `site.base_url` for absolute canonical/sitemap URLs; assets copied from `templates/site/default/assets/`; `site_build` row recorded. Command: `build` (`--dry-run`).
6. **GitHub Pages** (done): `core/siteval.py` validates generated output (required pages, sitemap/robots, JSON payloads, internal-link integrity, `.nojekyll`); `publish` rebuilds + validates per Ustaz (`--dry-run` validates committed output only); `.github/workflows/pages.yml` runs tests → doctor → `publish --all --dry-run`, then deploys `apps/` via `deploy-pages`. No server required.
7. **Local dashboard** (done): `manager/dashboard.py` (`collect_dashboard`, `format_dashboard`) — read-only over SQLite, never writes; per-Ustaz videos/pending/failed/downloaded, series count, last sync (`max(last_seen_at)`), latest `site_build` status, last 5 `error_log` rows. Command: `dashboard [id]`. Terminal only; it is not the public site.
8. Optional AI assistant (only after 1–6 stable).

Do not skip phases. After each phase: tests green, `doctor` green, update this file if boundaries changed.

### Discovery notes (Phase 2)

- Adapter: `core/sources/youtube.py` invokes the **yt-dlp binary** via `subprocess` argument arrays (never a shell string). Python module import is not required.
- Discovery is **metadata-only**; it never sets `download_status=COMPLETED`.
- Rediscovering a known video updates `title`/`metadata_hash`/`last_seen_at` only when the hash changes — identity stays `(ustaz_id, video_id)`.
- One failed source URL is logged and non-fatal; exit code is non-zero only if every source fails (or config is invalid).
- **Speaker attribution:** publisher channels often upload many speakers (e.g. PROmediaTAJDID). Optional per-Ustaz `content.speaker_names` (schema-optional list) filters discovery by case-insensitive substring on title/description — never channel name. Non-matching entries are counted as `skipped`, never stored. `discover --prune` explicitly deletes already-stored non-matching rows for that Ustaz (refused when the filter is unset; raw files under `data/raw/` are shared by `video_id` and left alone).
- Tests inject a fake `SourceAdapter` — do not add network calls to `tests/`.

### Download notes (Phase 3)

- Queue tables: `download_job` (per-video) + `jobs` (audit). `video.state` / `video.download_status` are authoritative.
- Claim is atomic single-worker: `PENDING → DOWNLOADING` then commit.
- Interrupted `DOWNLOADING` rows are reclaimed at start of `download`/`resume` (process not running).
- `retry-failed` resets `FAILED` jobs with `attempts < max_attempts` back to `PENDING`.
- Plugin seam: `core/downloader/base.py` (`Downloader`); production impl `core/downloader/ytdlp.py`.
- Sample config ships `download.mode=metadata` so default runs skip media; switch to `audio`/`video` per Ustaz when media is wanted.

### Classification / grouping notes (Phase 4)

- `classify_video()` is a pure function — unit-test the priority chain without DB.
- Rules live in `config/ustaz/<id>.json` → `classification.match_rules` (`{match: [...], category}`).
- LLM path is intentionally absent from the critical path; `classification.llm_fallback` may exist in universal config but must stay optional.
- Grouping never invents a series when there is no playlist and no `series_rules` match — video stays ungrouped (valid).
- Topic rules (`classification.series_rules`, first match wins) take precedence over playlists — this is how channel-only videos join a series and how mega-playlists (one playlist, many topics) split into topical series. Rule series titles identical to a playlist title merge into the same series (same slug).
- Idempotent: re-running `classify` skips `classification_status != PENDING` unless `--reclassify`; `group` only selects `series_id IS NULL`. `group --regroup` re-resolves every video (rules first), moves videos, drops stale series links (one archive series per video), and deletes series left with zero videos.

### Site generation notes (Phase 5)

- Engine: `render_template` in `core/sitegen.py` — `{{key}}` HTML-escaped, `{{>partial}}` includes from `templates/site/default/partials/`, pre-rendered fragments wrapped in `Raw`.
- Template root: `templates/site/default/page.html` + `partials/{head,header,footer}.html` + `assets/`.
- Output: `apps/<ustaz_id>/` (registry `output`); structure is directory-index friendly (`…/index.html`) for GitHub Pages.
- Absolute URLs only when `site.base_url` is set (schema-optional); otherwise relative/same-path locations in sitemap.
- Client search is vanilla JS against `data/search.json` (title/description/category/series/playlist) — no backend.
- Each successful build inserts into `site_build` (status, output_path, video_count, timestamps).
- Unit tests pass `config=`, `db_path=`, `output_dir=` — never touch the real DB or `apps/`.

### Publishing notes (Phase 6)

- `publish` = build + validate only. It never pushes or deploys; deploy flow is `publish` → `git add apps/<id>` → commit → push → Actions → Pages.
- Validator: `core/siteval.py` (`validate_site`, `validate_ustaz_output`) — stdlib only, no network. Fails on missing required files, malformed sitemap/JSON, or broken root-absolute internal links (`/...`); warns on leaked absolute filesystem paths.
- `.nojekyll` is emitted into every `apps/<id>/` so Pages serves underscore paths as-is.
- Workflow `.github/workflows/pages.yml` validates (`unittest`, `doctor`, `publish --all --dry-run`) before `upload-pages-artifact` (`apps/`) + `deploy-pages`. Enable once per repo: Settings → Pages → Source: "GitHub Actions".

### Rate limiting / pacing (conservative access only)

Config: `config/universal.json` → `youtube.rate_limit` (schema-validated).

| Knob | Role |
|------|------|
| `sleep_between_requests` / `max_sleep_between_requests` | Passed to yt-dlp `--sleep-requests` / `--max-sleep-interval` |
| `source_spacing_seconds` + jitter | Delay between source URLs in one discover run |
| `cooldown_seconds` | Forced pause after throttle signals (HTTP 429, “retry later”, bot-check text) or N consecutive errors |
| `max_consecutive_errors` | Abort pacing window after repeated failures |

Implementation: `core/rate_limit.py` (`RateLimitConfig`, `PaceGuard`, `backoff_delay`).

**Not implemented (spec forbids):** CAPTCHA/anti-bot bypass, user-agent/fingerprint spoofing, stealth automation, proxy rotation to evade limits, any human-mimicry intended to defeat detection (`todoagent.md` §26, §55, §62).
