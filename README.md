# Ustaz Archive Factory

Configuration-driven factory that builds independent Ustaz/Scholar archive websites from one shared Python pipeline. Spec: [`todoagent.md`](todoagent.md). Design: [`ARCHITECTURE.md`](ARCHITECTURE.md). Operator manual: [`USERGUIDE.md`](USERGUIDE.md).

## Requirements

- Python 3.10+ (stdlib only — no pip packages required for Phase 1)
- zsh
- yt-dlp on `PATH` (needed from Phase 2 discovery onward)

## Quick start

```zsh
./archive.zsh doctor    # validate env + configs + SQLite
./archive.zsh list      # show registry
./archive.zsh status    # per-Ustaz counts from the database
```

## Add a Ustaz (config only — no core code changes)

```zsh
./archive.zsh create ustaz_b \
  --name "Another Ustaz" \
  --channel "https://www.youtube.com/@another"
# edit config/ustaz/ustaz_b.json sources if needed
./archive.zsh doctor
./archive.zsh list
```

## Commands

| Command | Phase | Purpose |
|---------|-------|---------|
| `doctor` | 1 | Health check (configs, schemas, DB, dirs) |
| `list` | 1 | Registry contents |
| `status [id]` | 1 | Video counts |
| `dashboard [id]` | 7 | Operator view: counts, series, last sync, build status, recent failures (read-only) |
| `create <id>` | 1 | Scaffold config + register |
| `add <config>` | 1 | Register existing config file |
| `discover <id>` / `sync <id>` | 2 | YouTube metadata discovery (yt-dlp) |
| `download <id>` / `resume` / `retry-failed` | 3 | Download queue (state machine, validate, no redownload) |
| `classify <id>` / `group <id>` | 4 | Deterministic rules → category; playlist → archive series |
| `build <id>` | 5 | Static site → `apps/<id>/` (HTML, JSON, sitemap, robots, `.nojekyll`) |
| `publish <id>` | 6 | Rebuild + validate site (deploy-ready; then git commit/push → Actions → Pages) |
| `run` | 5+ | Stub until implemented (config still validated first) |

### Discovery examples

```zsh
./archive.zsh discover ustaz_a --dry-run          # what would change
./archive.zsh discover ustaz_a --limit 20         # cap entries per source
./archive.zsh discover ustaz_a --fast             # no spacing/retries (local)
./archive.zsh sync --all                          # all enabled Ustaz
```

### Download examples

```zsh
./archive.zsh download ustaz_a --dry-run          # queue counts only
./archive.zsh download ustaz_a --fast             # no pacing sleeps
./archive.zsh resume ustaz_a                      # reclaim interrupted jobs
./archive.zsh retry-failed ustaz_a                # re-queue FAILED under max_attempts
```

### Classify / group examples

```zsh
./archive.zsh classify ustaz_a --dry-run
./archive.zsh classify ustaz_a --reclassify       # force re-run
./archive.zsh group ustaz_a --dry-run
```

### Build examples

```zsh
./archive.zsh build ustaz_a --dry-run             # page counts only
./archive.zsh build ustaz_a                       # render apps/ustaz_a/
```

Optional `site.base_url` in the Ustaz config enables absolute canonical URLs and sitemap entries.

### Publish / Pages examples

```zsh
./archive.zsh publish ustaz_a --dry-run       # validate committed apps/ustaz_a/ only
./archive.zsh publish ustaz_a                 # rebuild + validate
./archive.zsh publish --all                   # every enabled Ustaz
# then: git add apps/ustaz_a && git commit -m "publish" && git push
```

`.github/workflows/pages.yml` runs tests → doctor → `publish --all --dry-run`,
then deploys `apps/` to GitHub Pages. One-time setup: repo Settings → Pages →
Source: "GitHub Actions".

Mode comes from `config/ustaz/<id>.json` → `download.mode` (`metadata`|`audio`|`video`). Sample config uses `metadata` (no media).

Requires `yt-dlp` on `PATH`. Discovery never downloads media.

Pacing (conservative access only) is configured in `config/universal.json` → `youtube.rate_limit`: request sleeps via yt-dlp `--sleep-requests`, inter-source spacing + jitter, and cooldown after throttle signals (429 / “retry later”). Stealth/evasion techniques are out of scope by design.

## Tests

```zsh
python3 -m unittest discover -s tests -v
```

Order when changing code: **tests → `./archive.zsh doctor`**.

## Layout

- `core/` — shared pipeline (config, SQLite, logging). Never put Ustaz-specific logic here.
- `manager/` — CLI + orchestration.
- `config/` — `universal.json`, `ustaz_registry.json`, `ustaz/<id>.json`.
- `schemas/` — JSON schemas; invalid config fails before any stage runs.
- `apps/<id>/` — generated static sites (`./archive.zsh build <id>`).
- `data/` — SQLite DB, raw media, normalized JSON (media/DB gitignored).

## Rules

- Video identity is `(ustaz_id, video_id)` — never title or filename.
- SQLite is the source of truth for state; never infer state from directories.
- Discovery ≠ download; never re-download known videos.
- No heavy frontend frameworks; no Tailwind; LLM optional and never required.
- Never hard-code a Ustaz ID inside `core/`.
