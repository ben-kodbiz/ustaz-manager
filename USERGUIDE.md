# Ustaz Archive Factory — Operator's Guide

Step-by-step manual for running the pipeline. Spec: `todoagent.md`.
Design: `ARCHITECTURE.md`. Commands reference at the bottom.

---

## 1. Requirements

| Need | Why |
|------|-----|
| Python 3.10+ (stdlib only, no pip packages) | The whole pipeline |
| zsh | `./archive.zsh` entrypoint |
| yt-dlp on `PATH` | Discovery + downloads (runs as a binary, never imported) |

Check everything at once:

```zsh
./archive.zsh doctor
```

`RESULT: OK` means environment, schemas, configs, database and directories
are all healthy. Fix anything marked `FAIL` before running stages.

---

## 2. Core concepts (read once)

- **One factory, many archives.** You never edit Python to add an Ustaz —
  you add `config/ustaz/<id>.json` and register it. Core code stays
  Ustaz-agnostic (no `if ustaz_id == ...` anywhere).
- **Video identity is `(ustaz_id, video_id)`.** Same YouTube video found via
  two sources is stored once. Titles are never identity.
- **SQLite is the source of truth** (`data/database/archive.db`).
  Never judge state by looking at directories.
- **Discovery ≠ download.** Discovery records metadata only. Download is a
  separate, opt-in stage and never re-downloads finished videos.
- **Source playlist ≠ archive series.** A YouTube playlist is preserved on
  the video (`playlist_id`); a *series* is your subject catalog
  (`series` table). Playlists *suggest* series; topic rules can override.
- **Every stage validates config first.** Invalid config fails fast with:
  `ERROR:` + filename + detail. Nothing half-runs.
- **Every mutating stage has `--dry-run`.** It reports, changes nothing.

---

## 3. Daily commands

```zsh
./archive.zsh list                 # registered Ustaz (id, enabled, output)
./archive.zsh status               # video counts per Ustaz from SQLite
./archive.zsh status qarni_edrus   # one Ustaz only
./archive.zsh dashboard            # operator view: counts, series,
                                   # last sync, build status, recent failures
```

`dashboard` is read-only — safe to run any time. It is the local operator
view, not the public website.

---

## 4. Adding a new Ustaz

```zsh
./archive.zsh create ustaz_b \
  --name "Ustaz Name" \
  --channel "https://www.youtube.com/@handle" \
  --language ms
./archive.zsh doctor
```

`create` scaffolds `config/ustaz/ustaz_b.json` from the template, registers
it in `config/ustaz_registry.json`, and makes `apps/ustaz_b/`. Accepted id
pattern: lowercase letter first, then letters/digits/underscore
(e.g. `qarni_edrus`).

Then edit `config/ustaz/ustaz_b.json`. The important blocks:

### 4.1 Sources — channels and playlists

```json
"sources": {
  "youtube_channels": ["https://www.youtube.com/@handle"],
  "youtube_playlists": [
    "https://www.youtube.com/playlist?list=PLxxxx"
  ]
}
```

- At least one channel or playlist is required (validated).
- **Prefer playlists for cataloging.** Channel listings carry no playlist
  membership, so channel-only videos can't auto-join a series — they rely
  on `series_rules` (§7) or stay singles. Every playlist you add becomes a
  browsable playlist page *and* suggests an archive series.
- Find a channel's playlists with:
  `https://www.youtube.com/channel/<UC-id>/playlists` (or `/@handle/playlists`)
  opened in a browser, or via yt-dlp `--flat-playlist --print "%(id)s | %(title)s"`.
- Very large channels (>~500 videos) can time out in one flat fetch. That
  is non-fatal (other sources still process), and the workaround is
  progressive capped runs: `--limit 150`, then `--limit 400`, then
  `--limit 1000`. Re-runs are idempotent — only new videos are added.

### 4.2 Speaker filter — multi-speaker publisher channels

Publisher channels (e.g. PROmediaTAJDID) upload many different speakers.
Without a filter, *everyone's* videos enter the archive. Restrict to your
Ustaz with name variants:

```json
"content": {
  "languages": ["ms"],
  "default_category": "ceramah",
  "speaker_names": ["Qarni Edrus", "Al-Qarni"]
}
```

- Optional. Absent/empty = keep everything (old behavior).
- Case-insensitive substring match against **title and description only**
  (channel name is the publisher, never the speaker).
- Non-matching entries are counted as `skipped`, never stored.
- List every spelling variant the channel uses
  ("Qarni Edrus" vs "Ahmad Al-Qarni Bin Edrus").
- Cleaning up after the fact: `discover --prune` deletes already-stored
  videos that fail the filter (explicit, logged; refused when the filter
  is unset; shared raw files untouched).

### 4.3 Classification rules (categories)

```json
"classification": {
  "enabled": true,
  "rules": { "playlist_priority": true, "title_priority": true,
             "description_priority": true },
  "match_rules": [
    { "match": ["tafsir", "surah", "ayat"], "category": "tafsir" },
    { "match": ["fiqh", "hukum"], "category": "fiqh" }
  ]
}
```

Priority: playlist title → video title → description → `default_category`
→ `Unclassified`. First matching rule wins. Rules are JSON and
case-insensitive — no AI involved, and none required.

### 4.4 Download mode

```json
"download": { "mode": "metadata" }
```

- `metadata` (default): catalog only, no media files. Start here.
- `audio` / `video`: download media to `data/raw/<id>/videos/` as
  `<date>_<video_id>_<slug>.<ext>`. Conservative pacing always applies;
  evasion tricks are out of scope by design (spec §26/§55/§62).

### 4.5 Site, persona, schedule

- `site.title` (+ optional `site.base_url` for absolute canonical/sitemap
  URLs), `persona` (display name, intro, style) control branding only.
- `schedule` is informational for now (`run` is still a stub).

---

## 5. Running the pipeline (in order)

```zsh
./archive.zsh discover qarni_edrus --dry-run   # 1. preview what would change
./archive.zsh discover qarni_edrus             # 1. real metadata harvest
./archive.zsh download qarni_edrus             # 2. queue (metadata/audio/video)
./archive.zsh classify qarni_edrus            # 3. categories
./archive.zsh group qarni_edrus               # 4. series
./archive.zsh build qarni_edrus               # 5. static site → apps/<id>/
./archive.zsh publish qarni_edrus --dry-run   # 6. validate deploy-readiness
```

Append `--all` instead of an id to run every enabled Ustaz.

### 5.1 Discover / sync — flags

| Flag | Effect |
|------|--------|
| `--dry-run` | List what would change; write nothing (DB rolled back) |
| `--limit N` | Cap entries per source (yt-dlp `--playlist-end`); use progressively (150 → 400 → 1000) on huge channels |
| `--fast` | No spacing/retries — local testing only |
| `--prune` | Delete stored videos failing `speaker_names` (refused when unset) |

Output line example:

```text
qarni_edrus: sources=9/9 new=98 updated=38 unchanged=87 skipped=224 pruned=0
```

`skipped` = speaker-filter rejects. One failed source is a warning, not a
fatal error — exit code fails only if *all* sources fail. Throttle signals
(429 / rate-limit text) trigger cooldown pauses automatically.

### 5.2 Download / resume / retry-failed

```zsh
./archive.zsh download qarni_edrus --dry-run   # queue counts only
./archive.zsh download qarni_edrus --limit 20  # cap jobs this run
./archive.zsh resume qarni_edrus               # reclaim interrupted jobs
./archive.zsh retry-failed qarni_edrus         # re-queue FAILED under max_attempts
```

State machine per video: `QUEUED → DOWNLOADING → DOWNLOADED|FAILED`.
`COMPLETED`/`SKIPPED` are never re-queued. One attempt per video per run.
Downloaded files are validated (exists, non-empty, `video_id` in name).

### 5.3 Classify

```zsh
./archive.zsh classify qarni_edrus --dry-run
./archive.zsh classify qarni_edrus --reclassify   # force re-run on done rows
```

Skips already-classified rows unless `--reclassify`. Failures land in
`Unclassified` per video — the run never aborts.

### 5.4 Group (series catalog)

```zsh
./archive.zsh group qarni_edrus --dry-run
./archive.zsh group qarni_edrus --regroup   # re-resolve everything (see §7)
```

- Playlist videos → series named after the playlist (source playlist and
  archive series stay in separate tables).
- `series_rules` (title match) win over playlists — see §7.
- No playlist and no rule match → video stays **ungrouped** (valid, shown
  under "Latest singles" on the site).

### 5.5 Build

```zsh
./archive.zsh build qarni_edrus --dry-run
./archive.zsh build qarni_edrus
```

Renders `apps/<id>/`: landing (hero + one card per series + latest
singles — the landing *is* the series/playlist catalog, so there are no
`/series` or `/playlists` index pages), about, search (offline JSON),
categories, per-video pages, `/series/<slug>/` and `/playlist/<slug>/`
detail pages, `sitemap.xml`, `robots.txt`, `.nojekyll`, `data/*.json`,
assets. Each success records a `site_build` row (shown on the dashboard).

### 5.6 Publish (validate deploy-readiness)

```zsh
./archive.zsh publish qarni_edrus --dry-run   # validate committed output only
./archive.zsh publish qarni_edrus             # rebuild + validate
```

`publish` never pushes anything itself. It checks required pages,
sitemap/robots, JSON payloads, `.nojekyll`, and that every internal link
resolves. Then you deploy:

```zsh
git add apps/qarni_edrus && git commit -m "publish qarni_edrus" && git push
# → GitHub Actions (tests → doctor → publish --all --dry-run) → Pages
```

One-time repo setup: Settings → Pages → Source: **GitHub Actions**.

---

## 6. Reviewing a site locally

```zsh
python3 -m http.server 9898 --directory apps/qarni_edrus
# open http://localhost:9898/
```

Serve the `apps/<id>/` directory itself as docroot (the site uses
root-absolute asset paths, so serving a parent folder would break CSS/JS).
Check home, a series page (episodes in order), search, and a video page.

---

## 7. Series rules cookbook (topic cataloging)

`series_rules` live under `classification` and apply by **first match wins**
— order specific phrases before general ones:

```json
"series_rules": [
  { "match": ["77 cabang iman"], "series": "77 Cabang Iman" },
  { "match": ["40 ayat tadabbur kewangan"], "series": "40 Ayat Tadabbur Kewangan Islam" },
  { "match": ["zikir nabawi"], "series": "Ustaz Qarni Edrus: Zikir Nabawi Selepas Solat" }
]
```

What they fix:

1. **Channel-only videos joining series.** Channel listings carry no
   playlist, so without a rule these stay singles. A rule puts
   `(4K) 15-12-2025 Ustaz Qarni Edrus : 77 Cabang Iman` into "77 Cabang Iman".
2. **Splitting mega-playlists.** One playlist holding many topics (e.g. a
   500-video "complete lectures" list) otherwise becomes one giant series.
   Rules carve it into topical series; leftovers keep the playlist series.
3. **Merging.** A rule `series` string identical to a playlist title lands
   in the *same* series (same slug) — channel singles and playlist episodes
   unite.

After editing rules, re-catalog existing rows (plain `group` only touches
ungrouped videos):

```zsh
./archive.zsh group qarni_edrus --regroup --dry-run   # preview moves
./archive.zsh group qarni_edrus --regroup             # move + drop empty series
./archive.zsh build qarni_edrus
```

`--regroup` re-resolves every video (rules first), enforces one series per
video (stale links removed), and deletes series left with zero videos.

To discover good rule phrases, look at recurring title clusters:

```zsh
python3 -c "
import sqlite3, re
from collections import Counter
conn = sqlite3.connect('data/database/archive.db')
titles = [r[0] for r in conn.execute(
    \"SELECT title FROM video WHERE ustaz_id='qarni_edrus'\")]
def core(t):
    t = (t or '').lower()
    t = re.sub(r'\(4k\)|\(live\)|🔴', '', t)
    t = re.sub(r'^\d[\d\w\s\-/,.\"“”]*ustaz\s+', '', t)
    return re.sub(r'\s+', ' ', t).strip(' :-,')[:60]
for topic, n in Counter(core(t) for t in titles).most_common(25):
    print(f'{n:4}  {topic}')
"
```

Cover spelling variants (`syu'ab` vs `syu’ab` curly quote, `hadis` vs
`hadith`, `Qarni Edrus` vs `Al-Qarni`) and re-run `group --regroup`.

---

## 8. Multi-channel operation & dedup

- Add any number of channels/playlists to `sources`; `speaker_names`
  applies to all of them automatically.
- Same video via two sources = one row (identity dedup, reported as
  `unchanged`).
- Same talk re-uploaded per channel gets distinct `video_id`s (distinct
  uploads). Audit with normalized-title + duration grouping; genuine
  re-uploads are rare — different channels normally carry different
  recordings.
- Cross-check per-channel contribution any time:

```zsh
python3 -c "
import sqlite3
from collections import Counter
conn = sqlite3.connect('data/database/archive.db')
rows = conn.execute(
    \"SELECT channel_name FROM video WHERE ustaz_id='qarni_edrus'\").fetchall()
print(dict(Counter(r[0] for r in rows)))
"
```

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `discover` retries then `yt-dlp timed out` on a huge channel | Single flat fetch too large. Non-fatal — other sources proceed. Re-run with `--limit 150/400/1000` progressively. |
| `ERROR: database is locked` in tests/another command | A discover/download is writing. Wait for it to finish; SQLite is single-writer. Never run two mutating stages at once. |
| `prune refused: content.speaker_names is not set` | Set `speaker_names` first — prune without a filter would wipe the archive, so it refuses. |
| `discover` shows `sources=0/N` | All sources failed (network/bot-check) or config invalid. `--fast` reduces backoff for diagnosis; check `logs/discovery.log`. |
| Videos stuck as singles | No playlist + no `series_rules` match. Add a rule (§7) + `group --regroup`. Check what they are: `SELECT title FROM video WHERE ustaz_id='X' AND (series_id IS NULL OR series_id='')`. |
| `Unclassified` pile-up | No `match_rules` hit and no `default_category`. Add match rules or accept the bucket (by design it never blocks the site). |
| `publish` reports broken internal links | Usually a series/playlist renamed without rebuild — just `build` again, then `publish`. |
| `doctor` FAIL on config | Read the `ERROR:` block: filename + reason. Common: empty sources, id/filename mismatch, bad enum (`mode` must be `metadata\|audio\|video`). |
| Wrong speaker's videos in archive | Publisher channel without (or with incomplete) `speaker_names`. Add variants, then `discover --prune`, rebuild. |

Logs live in `logs/` (one JSON object per line): `manager.log`,
`discovery.log`, `download.log`, `build.log`, `errors.log`.

---

## 10. Safety rules (non-negotiable)

- Pacing only via `core/rate_limit.py` + yt-dlp `--sleep-requests`.
  Never add user-agent/fingerprint spoofing, stealth automation, CAPTCHA
  bypass, or proxy rotation to evade limits (spec §26/§55/§62).
- Never `if ustaz_id == ...` in `core/` or `manager/` — configure, don't branch.
- Never infer state from directories; never hand-delete `data/`.
  Corrections go through commands (`--prune`, `--regroup`, `--reclassify`).
- Never commit media or the live DB (`data/raw/`, `data/database/*.db`
  are gitignored). Commit code, config, and `apps/<id>/` output.

---

## 11. Command reference

| Command | Example | Purpose |
|---------|---------|---------|
| `doctor` | `./archive.zsh doctor` | Health gate — run first and last |
| `list` | `./archive.zsh list` | Registry contents |
| `status [id]` | `./archive.zsh status qarni_edrus` | Video counts |
| `dashboard [id]` | `./archive.zsh dashboard` | Operator view (read-only) |
| `create <id>` | `./archive.zsh create ustaz_b --name "X" --channel <url>` | Scaffold + register |
| `add <config>` | `./archive.zsh add config/ustaz/x.json` | Register existing file |
| `discover/sync [id]` | `./archive.zsh discover qarni_edrus --limit 100 --prune` | Metadata harvest (`--all`, `--dry-run`, `--limit`, `--fast`, `--prune`) |
| `download [id]` | `./archive.zsh download qarni_edrus` | Queue (`resume`, `retry-failed`; `--dry-run`, `--limit`, `--fast`) |
| `classify [id]` | `./archive.zsh classify qarni_edrus --reclassify` | Categories (`--dry-run`) |
| `group [id]` | `./archive.zsh group qarni_edrus --regroup` | Series (`--dry-run`) |
| `build [id]` | `./archive.zsh build qarni_edrus` | Static site (`--dry-run`) |
| `publish [id]` | `./archive.zsh publish qarni_edrus --dry-run` | Validate deploy-readiness (`--all`) |
| `run` | — | Stub (config validated, not implemented) |

Verification order after any change: **tests → doctor**.

```zsh
python3 -m unittest discover -s tests -v
./archive.zsh doctor
```
