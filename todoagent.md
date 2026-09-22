# AgentODO — Ustaz Persona Archive App Factory

## 0. Mission

Build a reusable, open-source, GitHub-hosted system that can create and operate multiple independent Ustaz/Scholar archive websites from a single universal pipeline.

Example:

```text
                    ┌─────────────────────────────┐
                    │      UNIVERSAL BRAIN        │
                    │     Archive Manager         │
                    │                             │
                    │ config / scheduler          │
                    │ discovery / classification  │
                    │ state / validation          │
                    │ site generation             │
                    └──────────────┬──────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
       ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
       │ USTAZ APP A  │     │ USTAZ APP B  │     │ USTAZ APP C  │
       │              │     │              │     │              │
       │ config.json  │     │ config.json  │     │ config.json  │
       │ generated    │     │ generated    │     │ generated    │
       │ pages/data   │     │ pages/data   │     │ pages/data   │
       └──────────────┘     └──────────────┘     └──────────────┘
```

The first implementation should be able to support one Ustaz.

The architecture must make adding the second, third, tenth, or hundredth Ustaz primarily a **configuration operation**, not a software-development operation.

---

# 1. Core Requirements

## Required

The system MUST:

* be open source
* use free/open-source components
* be GitHub-friendly
* support GitHub Pages for generated static websites
* use lightweight HTML/CSS/JavaScript
* avoid heavy frontend frameworks
* avoid Tailwind
* avoid unnecessary Node/npm dependency
* use Python for backend/pipeline automation where practical
* use SQLite for operational state
* use JSON for universal configuration and generated metadata
* optionally export CSV/Markdown
* use yt-dlp for public YouTube discovery/download
* support YouTube channel URLs
* support YouTube playlist URLs
* discover video IDs systematically
* track every discovered video
* track download state
* support resumable processing
* avoid downloading the same video repeatedly
* organize videos by playlist
* organize related ceramah into series/topics
* automatically generate archive pages
* automatically generate search/filter metadata
* generate a separate configuration for every Ustaz app
* maintain one universal configuration
* have a central manager/brain controlling all Ustaz instances
* allow independent Ustaz apps to be rebuilt without modifying the core pipeline
* support adding new Ustaz personas later
* be modular
* be testable
* be recoverable after failure

---

# 2. Important Architectural Principle

## DO NOT build this

```text
ustaz-a/
  custom code

ustaz-b/
  custom code

ustaz-c/
  custom code
```

That creates maintenance hell.

## Build this instead

```text
ustaz-archive-factory/
│
├── core/
│   ├── discovery/
│   ├── downloader/
│   ├── metadata/
│   ├── classifier/
│   ├── grouping/
│   ├── generator/
│   ├── validator/
│   └── publisher/
│
├── manager/
│   ├── scheduler
│   ├── orchestrator
│   └── registry
│
├── config/
│   ├── universal.json
│   └── ustaz/
│       ├── ustaz-a.json
│       ├── ustaz-b.json
│       └── ustaz-c.json
│
├── templates/
│   └── site/
│
├── apps/
│   ├── ustaz-a/
│   ├── ustaz-b/
│   └── ustaz-c/
│
├── data/
│   ├── database/
│   ├── raw/
│   ├── normalized/
│   └── generated/
│
└── scripts/
```

The **core is shared**.

The **configuration identifies the Ustaz**.

The **generated app contains generated data/site output**, not another copy of the business logic.

---

# 3. Universal Brain

Create a central manager called:

```text
Archive Manager
```

or internally:

```text
Ustaz Archive Brain
```

Its responsibility is orchestration.

It should NOT directly contain Ustaz-specific rules.

Instead:

```text
Manager
   │
   ├── read universal config
   │
   ├── load Ustaz registry
   │
   ├── load individual Ustaz config
   │
   ├── discover
   │
   ├── synchronize metadata
   │
   ├── download missing content
   │
   ├── classify/group
   │
   ├── generate site data
   │
   ├── generate static website
   │
   └── publish
```

---

# 4. Ustaz Registry

Create:

```text
config/ustaz_registry.json
```

Example:

```json
{
  "ustaz": [
    {
      "id": "ustaz_a",
      "name": "Example Ustaz",
      "enabled": true,
      "config": "config/ustaz/ustaz_a.json",
      "output": "apps/ustaz_a"
    },
    {
      "id": "ustaz_b",
      "name": "Another Ustaz",
      "enabled": false,
      "config": "config/ustaz/ustaz_b.json",
      "output": "apps/ustaz_b"
    }
  ]
}
```

The manager loops through enabled Ustaz configurations.

---

# 5. Universal Configuration

Create:

```text
config/universal.json
```

This controls global behavior.

Example:

```json
{
  "project": {
    "name": "ustaz-archive-factory",
    "version": "0.1.0"
  },

  "runtime": {
    "timezone": "Asia/Kuala_Lumpur",
    "language": "en",
    "dry_run": false
  },

  "storage": {
    "database": "data/database/archive.db",
    "downloads": "data/raw",
    "metadata": "data/normalized",
    "generated": "data/generated"
  },

  "youtube": {
    "enabled": true,
    "download": true,
    "audio_only": false,
    "archive_metadata": true
  },

  "processing": {
    "max_workers": 1,
    "retry_count": 3,
    "backoff_seconds": 30
  },

  "classification": {
    "rules_first": true,
    "llm_fallback": false
  },

  "site": {
    "generator": "static",
    "frontend": "vanilla",
    "css": "pico"
  },

  "publishing": {
    "github_pages": true
  }
}
```

Global configuration MUST NOT contain Ustaz-specific information.

---

# 6. Individual Ustaz Configuration

Each Ustaz receives:

```text
config/ustaz/<ustaz-id>.json
```

Example:

```json
{
  "id": "ustaz_a",

  "identity": {
    "name": "Example Ustaz",
    "slug": "example-ustaz",
    "description": "Islamic lectures and educational talks"
  },

  "sources": {
    "youtube_channels": [
      "https://www.youtube.com/@example"
    ],

    "youtube_playlists": []
  },

  "content": {
    "languages": [
      "ms"
    ],

    "default_category": "ceramah"
  },

  "classification": {
    "enabled": true,

    "rules": {
      "playlist_priority": true,
      "title_priority": true,
      "description_priority": true
    }
  },

  "site": {
    "title": "Example Ustaz Archive",
    "theme": "default"
  },

  "schedule": {
    "enabled": true,
    "interval": "daily"
  }
}
```

Adding another Ustaz should ideally require only:

```text
create config
register config
run manager
```

---

# 7. YouTube Discovery Pipeline

The discovery pipeline MUST separate:

```text
DISCOVERY
```

from:

```text
DOWNLOAD
```

This is critical.

The system should first discover and record video metadata.

Only later should the downloader decide what needs downloading.

Pipeline:

```text
YouTube source
      │
      ▼
yt-dlp metadata discovery
      │
      ▼
video ID
      │
      ▼
database lookup
      │
      ├── already known → update metadata
      │
      └── new → insert
```

Never use filename as the primary identity.

The canonical identity is:

```text
YouTube video ID
```

---

# 8. Video Record

Every video should have a database record similar to:

```text
video_id
ustaz_id
title
original_title
description
channel_id
channel_name
playlist_id
playlist_title
upload_date
duration
thumbnail_url
web_url

download_status
download_path

classification_status
category
series_id

first_seen_at
last_seen_at
downloaded_at

metadata_hash
error_count
last_error
```

---

# 9. State Machine

Every video MUST have an explicit state.

Suggested states:

```text
DISCOVERED
     │
     ▼
METADATA_READY
     │
     ▼
QUEUED
     │
     ▼
DOWNLOADING
     │
     ├── FAILED
     │      │
     │      └── RETRY
     │
     ▼
DOWNLOADED
     │
     ▼
CLASSIFIED
     │
     ▼
GROUPED
     │
     ▼
INDEXED
     │
     ▼
PUBLISHED
```

Do not rely on directory existence to determine state.

SQLite is the source of truth.

---

# 10. Duplicate Prevention

Duplicate prevention MUST happen using:

```text
ustaz_id + video_id
```

NOT:

```text
title
```

Titles are unreliable.

Example:

```text
"TAZKIRAH RAMADAN"
"TAZKIRAH RAMADAN 2025"
"Tazkirah Ramadan"
```

could be different videos.

The YouTube ID determines identity.

---

# 11. Playlist Processing

The system must preserve YouTube playlist information.

Example:

```text
Playlist:
  Tafsir Surah Al-Baqarah

Videos:
  001
  002
  003
  004
```

Generated site:

```text
/playlist/tafsir-surah-al-baqarah/
```

with:

```text
Episode 1
Episode 2
Episode 3
Episode 4
```

The pipeline should preserve:

```text
playlist_id
playlist_title
playlist_position
```

when available.

---

# 12. Same-Subject Grouping

A second grouping layer is required.

YouTube playlists are NOT necessarily the same thing as subject/series.

Example:

```text
YouTube Playlist
        │
        ▼
"2024 Kuliah Maghrib"
        │
        ├── Aqidah
        ├── Fiqh
        ├── Akhlak
        └── Tafsir
```

The system therefore needs:

```text
SOURCE PLAYLIST
```

and:

```text
ARCHIVE SERIES
```

as separate concepts.

---

# 13. Classification Strategy

Use a deterministic pipeline first.

Priority:

```text
1. Explicit manual rules
2. Playlist metadata
3. Title patterns
4. Description
5. Existing series rules
6. Optional AI/LLM classification
7. "Unclassified"
```

Do NOT make an LLM mandatory.

A classification failure must never stop the archive.

Example:

```text
Unknown subject
```

is acceptable.

The video can later be reclassified.

---

# 14. Classification Rules

Support configuration such as:

```json
{
  "rules": [
    {
      "match": [
        "tafsir",
        "surah",
        "ayat"
      ],
      "category": "tafsir"
    },
    {
      "match": [
        "fiqh",
        "hukum"
      ],
      "category": "fiqh"
    }
  ]
}
```

Rules must be deterministic and testable.

---

# 15. Optional LLM Layer

LLM classification is optional.

Architecture:

```text
Rules
  │
  ├── confident → accept
  │
  └── ambiguous
          │
          ▼
     Optional LLM
          │
          ▼
     suggested category
          │
          ▼
      validation
```

The LLM must never become a hard dependency.

The archive should work without AI.

---

# 16. Subject/Series Model

Create a normalized series record:

```text
series_id
ustaz_id
slug
title
description
category
language
created_at
updated_at
```

Example:

```text
series:
  ustaz-a/tafsir-surah-al-kahf
```

Videos then reference:

```text
series_id
```

---

# 17. Generated Website

Each Ustaz should receive a generated static website.

Example:

```text
apps/
└── ustaz_a/
    ├── index.html
    ├── about.html
    ├── search.html
    │
    ├── series/
    │   ├── index.html
    │   └── tafsir-surah-kahf/
    │       └── index.html
    │
    ├── playlists/
    │
    ├── videos/
    │
    ├── categories/
    │
    ├── assets/
    │
    └── data/
        ├── videos.json
        ├── series.json
        ├── playlists.json
        └── categories.json
```

---

# 18. Frontend Philosophy

Keep the frontend extremely lightweight.

Preferred stack:

```text
HTML
CSS
Vanilla JavaScript
Pico.css or similarly lightweight CSS
JSON
```

Avoid:

```text
React
Angular
Vue
Next.js
Nuxt
Svelte
Tailwind
large UI frameworks
```

The archive is primarily content.

It does not need a huge frontend framework.

---

# 19. Static Site Architecture

The website should load generated JSON.

Example:

```text
index.html
    │
    └── app.js
          │
          └── data/videos.json
```

Search:

```text
search.js
    │
    └── videos.json
```

Series:

```text
series.js
    │
    └── series.json
```

This makes the website:

* cheap
* portable
* GitHub Pages compatible
* easy to backup
* easy to mirror
* easy to deploy

---

# 20. Automatic Page Generation

The generator should automatically create pages based on available content.

Examples:

```text
/
 /about
 /categories
 /playlists
 /series
 /search
 /video/<id>
 /series/<slug>
 /playlist/<slug>
 /category/<slug>
```

If a new category appears:

```text
fiqh
```

generate:

```text
/category/fiqh/
```

If a new series appears:

```text
tafsir-surah-yasin
```

generate:

```text
/series/tafsir-surah-yasin/
```

No manual HTML editing should be required.

---

# 21. Persona Layer

The system should support a configurable persona/identity layer.

This is NOT intended to imitate the person.

It controls how the archive website presents the scholar's actual material.

Example:

```json
{
  "persona": {
    "display_name": "Example Ustaz",
    "short_description": "...",
    "intro_text": "...",
    "preferred_categories": [
      "ceramah",
      "tafsir",
      "fiqh"
    ],
    "site_style": "traditional"
  }
}
```

The persona configuration controls:

* branding
* description
* navigation
* categories
* introductory text
* visual theme
* terminology
* metadata presentation

The underlying pipeline remains identical.

---

# 22. Separate Identity From Pipeline

This is mandatory.

```text
CORE PIPELINE
      │
      ├── Discovery
      ├── Download
      ├── Metadata
      ├── Classification
      ├── Grouping
      ├── Generator
      └── Publisher

IDENTITY CONFIG
      │
      ├── Ustaz name
      ├── Sources
      ├── Categories
      ├── Persona
      ├── Theme
      └── Schedule
```

Never hard-code:

```text
if ustaz == "X":
```

inside the core pipeline.

Use configuration or plugins.

---

# 23. Database

Use SQLite initially.

Recommended:

```text
SQLite
SQLite FTS5
```

Database:

```text
data/database/archive.db
```

Tables:

```text
ustaz
source
playlist
video
series
category
video_series
video_category
download_job
processing_job
error_log
site_build
```

---

# 24. SQLite FTS5

Use FTS5 for local archive searching.

Search fields:

```text
title
description
playlist_title
series_title
category
```

Example:

```text
search "solat"
```

should return matching videos/series.

Do not introduce Elasticsearch/OpenSearch unless the project genuinely outgrows SQLite.

---

# 25. Download Manager

The download system should:

* use yt-dlp
* record every job in SQLite
* support resumable downloads
* avoid duplicate downloads
* retry temporary failures
* preserve useful metadata
* validate completed files
* record final file path
* allow manual retry
* allow failed jobs to be inspected

Suggested states:

```text
PENDING
DOWNLOADING
COMPLETED
FAILED
SKIPPED
```

---

# 26. Rate Limiting and Failure Handling

The system MUST be conservative.

Implement:

```text
request spacing
retry limits
exponential backoff
jitter
cooldown
single-worker default
```

Do NOT implement:

```text
CAPTCHA bypass
anti-bot bypass
fingerprint spoofing
stealth automation
proxy rotation to evade limits
```

A configured proxy may be supported for legitimate network routing, but it must not be used to circumvent platform restrictions.

---

# 27. Download Modes

Support:

```text
metadata-only
audio
video
```

Configuration example:

```json
{
  "download": {
    "mode": "video"
  }
}
```

Possible future values:

```text
metadata
audio
video
```

---

# 28. Storage Layout

Use deterministic paths.

Example:

```text
data/
├── raw/
│   └── ustaz_a/
│       └── videos/
│
├── normalized/
│   └── ustaz_a/
│       ├── videos.json
│       ├── playlists.json
│       └── series.json
│
├── generated/
│   └── ustaz_a/
│
└── database/
    └── archive.db
```

Never create random filenames.

Recommended video filename:

```text
<upload-date>_<video-id>_<slug>.mp4
```

The video ID remains the immutable identifier.

---

# 29. Metadata Preservation

For every discovered video preserve the original metadata before normalization.

Store:

```text
raw metadata
```

and:

```text
normalized metadata
```

Example:

```text
raw/
  youtube/<video-id>.json

normalized/
  videos.json
```

This allows the normalization algorithm to evolve without rediscovering everything.

---

# 30. Incremental Operation

The pipeline must be incremental.

Running:

```zsh
./scripts/run.zsh ustaz_a
```

tomorrow should NOT process everything again.

It should effectively perform:

```text
discover
   ↓
find new/changed records
   ↓
process only required work
   ↓
rebuild affected pages
```

---

# 31. Daily Synchronization

Example:

```text
Daily
  │
  ▼
Discover channel
  │
  ▼
New video?
  │
  ├── NO → update metadata only if needed
  │
  └── YES
       │
       ▼
     Queue
       │
       ▼
     Download
       │
       ▼
   Classify
       │
       ▼
     Group
       │
       ▼
 Generate pages
       │
       ▼
 Publish
```

---

# 32. Monthly/Periodic Full Reconciliation

Support:

```text
sync
```

and:

```text
reconcile
```

Difference:

### sync

Find newly added content.

### reconcile

Check existing archive against source metadata.

Reconciliation should detect:

* changed titles
* changed playlist membership
* missing metadata
* broken local files
* stale generated pages
* inconsistent database records

---

# 33. Manager CLI

Provide a simple CLI.

Examples:

```zsh
./archive.zsh list
```

```zsh
./archive.zsh add ustaz_a
```

```zsh
./archive.zsh discover ustaz_a
```

```zsh
./archive.zsh download ustaz_a
```

```zsh
./archive.zsh classify ustaz_a
```

```zsh
./archive.zsh build ustaz_a
```

```zsh
./archive.zsh publish ustaz_a
```

```zsh
./archive.zsh sync ustaz_a
```

```zsh
./archive.zsh sync --all
```

```zsh
./archive.zsh status
```

```zsh
./archive.zsh retry-failed
```

```zsh
./archive.zsh doctor
```

---

# 34. One Command Full Pipeline

Provide:

```zsh
./archive.zsh run ustaz_a
```

Equivalent to:

```text
discover
   ↓
metadata
   ↓
download
   ↓
validate
   ↓
classify
   ↓
group
   ↓
generate
   ↓
validate site
   ↓
publish
```

For everything:

```zsh
./archive.zsh run --all
```

---

# 35. Manager Dashboard

Phase 2 can provide a tiny local web dashboard.

Example:

```text
Archive Manager
──────────────────────────────

Ustaz              Videos    New    Failed
------------------------------------------------
Ustaz A             2,431      7       1
Ustaz B             1,827      3       0
Ustaz C               942     12       4

Last synchronization:
2026-09-22 03:00

[Run All]
[Discover]
[Downloads]
[Failures]
[Build Sites]
```

Keep this dashboard lightweight.

It is NOT the public Ustaz website.

---

# 36. Dependency Graph

The manager should understand dependencies.

```text
DISCOVERY
   │
   ▼
METADATA
   │
   ▼
DOWNLOAD
   │
   ▼
VALIDATION
   │
   ▼
CLASSIFICATION
   │
   ▼
GROUPING
   │
   ▼
INDEX
   │
   ▼
SITE GENERATION
   │
   ▼
SITE VALIDATION
   │
   ▼
PUBLISH
```

A failed download must NOT prevent metadata indexing.

A failed classification must NOT destroy the website.

A failed publish must NOT invalidate the local archive.

---

# 37. Job Queue

Initially avoid Redis/Celery/etc.

SQLite is enough.

Use:

```text
jobs
```

table.

Example:

```text
job_id
ustaz_id
video_id
job_type
status
priority
attempts
created_at
started_at
finished_at
error
```

Job types:

```text
DISCOVER
DOWNLOAD
VALIDATE
CLASSIFY
GROUP
BUILD
PUBLISH
```

This keeps the system portable.

---

# 38. Plugin Architecture

Core plugin boundaries:

```text
SourceAdapter
Downloader
MetadataNormalizer
Classifier
SeriesResolver
SiteGenerator
Publisher
```

Example:

```text
sources/
    youtube.py

downloaders/
    ytdlp.py

classifiers/
    rules.py
    optional_llm.py

generators/
    static.py

publishers/
    github_pages.py
```

Future sources can therefore become:

```text
sources/
    youtube.py
    archive_org.py
    local.py
```

without rewriting the pipeline.

---

# 39. GitHub Architecture

Recommended repository:

```text
ustaz-archive-factory
```

Structure:

```text
ustaz-archive-factory/
│
├── README.md
├── LICENSE
├── AGENTS.md
├── agentodo.md
│
├── config/
├── core/
├── manager/
├── templates/
├── scripts/
├── tests/
│
├── apps/
│   ├── ustaz_a/
│   └── ustaz_b/
│
└── data/
```

Do NOT commit huge video files into Git.

GitHub stores:

```text
code
config
metadata
generated static site
```

Large media should be handled separately according to the project's chosen legal/storage arrangement.

---

# 40. Generated App Repository Strategy

Support two modes.

## Mode A — Monorepo

```text
factory/
  apps/
    ustaz_a/
    ustaz_b/
    ustaz_c/
```

Simple initially.

## Mode B — Separate Repositories

Example:

```text
ustaz-archive-factory
ustaz-a-archive
ustaz-b-archive
ustaz-c-archive
```

The factory generates each site's static output.

Prefer Mode A during development.

Move to separate repositories when the number of Ustaz projects justifies it.

---

# 41. GitHub Pages

Generated apps should be deployable to:

```text
GitHub Pages
```

using GitHub Actions.

Pipeline:

```text
Factory
   │
   ▼
Build static website
   │
   ▼
Validate
   │
   ▼
Git commit
   │
   ▼
GitHub Actions
   │
   ▼
GitHub Pages
```

Do not require a permanent server for the public website.

---

# 42. Generated Configuration

When creating a new Ustaz:

```zsh
./archive.zsh create ustaz_a
```

the manager should generate:

```text
config/ustaz/ustaz_a.json
```

and optionally:

```text
apps/ustaz_a/
```

from templates.

Prompt/configuration fields:

```text
Ustaz name
slug
YouTube channel
playlists
language
categories
description
theme
download mode
schedule
```

---

# 43. Configuration Inheritance

Use:

```text
universal config
       │
       ▼
Ustaz config
       │
       ▼
optional local override
```

Example:

```text
config/universal.json
       ↓
config/ustaz/ustaz_a.json
       ↓
config/ustaz/ustaz_a.local.json
```

Do not duplicate universal settings unnecessarily.

---

# 44. Schema Validation

All configuration must be validated before execution.

Create:

```text
schemas/
```

with:

```text
universal.schema.json
ustaz.schema.json
registry.schema.json
```

Invalid configuration must fail early.

Example:

```text
ERROR:
ustaz_a.json

sources.youtube_channels is required
```

Do not start a download job with invalid configuration.

---

# 45. Site Template

Create one reusable template:

```text
templates/site/default/
```

Structure:

```text
templates/site/default/
├── index.html
├── category.html
├── series.html
├── playlist.html
├── video.html
├── search.html
├── about.html
├── assets/
│   ├── css/
│   └── js/
└── partials/
```

The generator injects:

```text
Ustaz identity
content
metadata
navigation
theme
```

---

# 46. Theme System

Themes should be configuration-driven.

Example:

```json
{
  "theme": {
    "name": "default",
    "layout": "classic",
    "density": "comfortable"
  }
}
```

Potential themes:

```text
default
minimal
traditional
archive
```

Do not create separate frontend applications for every theme.

---

# 47. SEO

Generated pages should automatically include:

```text
title
description
canonical URL
OpenGraph metadata
structured metadata where appropriate
sitemap.xml
robots.txt
```

Each video/series/category should receive a unique page title.

Example:

```text
Example Ustaz — Tafsir Surah Al-Kahf — Episode 4
```

---

# 48. Search

Phase 1:

```text
client-side JSON + JavaScript
```

Phase 2, if archive becomes large:

```text
pre-generated search index
```

Do not introduce a server-side search service unless required.

---

# 49. Content Integrity

Every processed video should be validated.

Checks:

```text
file exists
file size > 0
expected extension
metadata exists
database record exists
video ID matches
```

Optional:

```text
SHA-256
duration validation
ffprobe validation
```

---

# 50. Recovery

The system must survive:

```text
power failure
network failure
process termination
partial download
failed Git push
failed site build
failed classification
```

Every stage must be restartable.

Example:

```zsh
./archive.zsh resume
```

should continue unfinished work.

---

# 51. Logging

Use structured logs.

Example:

```text
logs/
├── manager.log
├── discovery.log
├── download.log
├── build.log
└── errors.log
```

Every log entry should identify:

```text
timestamp
ustaz_id
video_id
job_id
stage
message
```

---

# 52. Dry Run

Every destructive/large operation should support:

```zsh
--dry-run
```

Example:

```zsh
./archive.zsh sync ustaz_a --dry-run
```

Output:

```text
NEW:
  7 videos

DOWNLOAD:
  7 videos

ALREADY EXISTS:
  2,431 videos

FAILED:
  0
```

No files are changed.

---

# 53. Testing

Tests are mandatory.

Minimum:

```text
tests/
├── test_config.py
├── test_discovery.py
├── test_deduplication.py
├── test_classification.py
├── test_grouping.py
├── test_generator.py
├── test_state_machine.py
└── test_pipeline.py
```

Important test cases:

### Duplicate video

Same YouTube ID discovered twice.

Expected:

```text
one database record
```

### Changed title

Expected:

```text
metadata updated
video not downloaded again
```

### Failed download

Expected:

```text
FAILED state
retry available
```

### Interrupted download

Expected:

```text
resume
```

### New playlist

Expected:

```text
playlist automatically appears
```

### New series

Expected:

```text
series page automatically generated
```

---

# 54. Security

Never execute arbitrary metadata as shell commands.

Treat:

```text
YouTube titles
descriptions
playlist names
channel names
```

as untrusted input.

Sanitize filenames and URLs.

Do not construct shell commands by string concatenation.

Prefer Python subprocess argument arrays.

---

# 55. Content/Legal Safety

The system should archive content only where the project has a legitimate basis to do so.

The pipeline must respect:

* source/platform terms
* copyright
* permissions
* applicable law
* creator rights

Do not build mechanisms intended to bypass YouTube restrictions or anti-bot controls.

The system should support metadata-only operation where downloading is not appropriate.

---

# 56. AI/Agent Integration

AI should sit ABOVE the deterministic pipeline.

Recommended:

```text
                  ┌──────────────┐
                  │  AI Manager  │
                  │  optional    │
                  └──────┬───────┘
                         │
                         ▼
                  ┌──────────────┐
                  │ Core Engine  │
                  └──────┬───────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
          discovery   download   generator
```

The AI agent can:

* inspect status
* identify ambiguous classification
* propose grouping
* inspect failures
* generate configuration
* generate descriptions
* suggest corrections

The AI agent should NOT own the database state machine.

Deterministic code remains authoritative.

---

# 57. Manager/Brain Responsibilities

The Manager should eventually be capable of:

```text
"Synchronize all Ustaz archives."

"Show me failed downloads."

"Create a new Ustaz archive."

"Rebuild Ustaz A."

"Find videos that are not classified."

"Reconcile playlist changes."

"Generate a report."
```

But internally these become deterministic commands.

Example:

```text
USER REQUEST
     ↓
MANAGER
     ↓
PLAN
     ↓
VALIDATE
     ↓
EXECUTE CORE JOBS
     ↓
REPORT
```

---

# 58. Reports

Generate:

```text
reports/
```

Example:

```text
ustaz_a-status.json
ustaz_a-status.md
```

Report:

```text
Total videos:       2,431
Downloaded:         2,417
Pending:                7
Failed:                 7
Classified:         2,399
Unclassified:          32
Series:                81
Playlists:             43

Last sync:
2026-09-22
```

---

# 59. Data Flow

Complete system:

```text
                    UNIVERSAL CONFIG
                           │
                           ▼
                    USTAZ REGISTRY
                           │
                           ▼
                    USTAZ CONFIG
                           │
                           ▼
                    ┌──────────────┐
                    │   MANAGER    │
                    └──────┬───────┘
                           │
                           ▼
                    SOURCE DISCOVERY
                           │
                           ▼
                       METADATA
                           │
                           ▼
                       SQLITE
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
          DOWNLOAD     CLASSIFY      GROUP
              │            │            │
              └────────────┼────────────┘
                           ▼
                     NORMALIZED DATA
                           │
                           ▼
                    STATIC GENERATOR
                           │
                           ▼
                     SITE VALIDATOR
                           │
                           ▼
                     GIT/GITHUB
                           │
                           ▼
                    GITHUB PAGES
```

---

# 60. Development Phases

## Phase 1 — Foundation

Implement:

* repository structure
* universal config
* Ustaz config
* registry
* SQLite
* configuration validation
* CLI
* logging

Success:

```zsh
./archive.zsh doctor
```

works.

---

## Phase 2 — YouTube Discovery

Implement:

* yt-dlp adapter
* channel discovery
* playlist discovery
* video metadata extraction
* ID deduplication
* SQLite persistence

Success:

```zsh
./archive.zsh discover ustaz_a
```

produces correct records.

---

## Phase 3 — Download Manager

Implement:

* queue
* state machine
* resumable downloads
* retries
* validation
* download tracking

Success:

```zsh
./archive.zsh download ustaz_a
```

can stop and resume safely.

---

## Phase 4 — Classification

Implement:

* rules
* categories
* playlist grouping
* series
* subject grouping
* unclassified bucket

Success:

```text
all videos have deterministic classification state
```

---

## Phase 5 — Static Generator

Implement:

* template engine
* HTML generation
* JSON generation
* category pages
* playlist pages
* series pages
* video pages
* search
* sitemap

Success:

```zsh
./archive.zsh build ustaz_a
```

creates a complete static website.

---

## Phase 6 — GitHub Pages

Implement:

* GitHub repository output
* GitHub Actions
* Pages deployment
* generated site validation

Success:

```text
git push
   ↓
GitHub Actions
   ↓
GitHub Pages
```

---

## Phase 7 — Manager Dashboard

Implement lightweight dashboard.

Display:

```text
Ustaz
Videos
Downloads
Failures
Series
Last sync
Build status
```

---

## Phase 8 — AI Assistant

Only after the deterministic system is stable.

Implement optional:

```text
classification assistant
grouping assistant
failure assistant
configuration assistant
```

---

# 61. Definition of Done

The project is considered MVP-complete when:

* [ ] one Ustaz can be defined entirely through config
* [ ] YouTube channel can be discovered
* [ ] video IDs are persisted
* [ ] duplicate videos are detected
* [ ] metadata is preserved
* [ ] playlists are preserved
* [ ] downloads are tracked
* [ ] interrupted downloads can resume
* [ ] failed jobs can retry
* [ ] videos can be classified
* [ ] videos can be grouped into series
* [ ] static pages are generated automatically
* [ ] search works
* [ ] generated website works without backend
* [ ] GitHub Pages deployment works
* [ ] adding a second Ustaz requires no core-code modification
* [ ] configuration is schema validated
* [ ] pipeline is testable
* [ ] pipeline can run without an LLM
* [ ] documentation exists

---

# 62. Agent Execution Rules

When an AI coding agent works on this repository:

1. Read `README.md`.
2. Read `agentodo.md`.
3. Inspect existing architecture before changing code.
4. Never rewrite working components unnecessarily.
5. Preserve configuration-driven architecture.
6. Never hard-code a specific Ustaz into core logic.
7. Never introduce a heavy frontend framework without explicit justification.
8. Prefer Python standard library where practical.
9. Prefer SQLite over adding another database.
10. Prefer JSON for portable configuration/data exchange.
11. Use zsh scripts for Linux developer commands.
12. Add tests with significant changes.
13. Run tests after modifications.
14. Run configuration validation before pipeline execution.
15. Never silently delete existing archive data.
16. Never silently redownload existing videos.
17. Never bypass platform restrictions.
18. Keep each pipeline stage independently restartable.
19. Update documentation when architecture changes.
20. Report exactly what was changed and what was tested.

---

# 63. First Agent Task

Do NOT immediately implement the entire system.

First inspect the repository and produce:

```text
ARCHITECTURE.md
```

containing:

* current repository structure
* proposed architecture
* dependency graph
* database schema
* configuration schema
* module boundaries
* data flow
* state machine
* GitHub deployment model

Then implement only:

```text
Phase 1 — Foundation
```

After Phase 1 passes tests, proceed to Phase 2.

Do not skip phases.

---

# 64. First MVP Command

The ultimate first milestone is:

```zsh
./archive.zsh run ustaz_a
```

which performs:

```text
CONFIG
  ↓
DISCOVER
  ↓
DATABASE
  ↓
DOWNLOAD
  ↓
CLASSIFY
  ↓
GROUP
  ↓
GENERATE
  ↓
VALIDATE
```

and produces:

```text
apps/ustaz_a/
```

containing a working static archive.

Then:

```zsh
./archive.zsh run ustaz_b
```

must produce:

```text
apps/ustaz_b/
```

using the SAME core pipeline.

That proves the architecture is genuinely reusable.

---

# 65. Long-Term Goal

The final system should behave like a small **content archive factory**:

```text
                         ┌──────────────────────┐
                         │  USTAZ ARCHIVE BRAIN  │
                         └──────────┬───────────┘
                                    │
                  ┌─────────────────┼─────────────────┐
                  │                 │                 │
                  ▼                 ▼                 ▼
             USTAZ A            USTAZ B            USTAZ C
             CONFIG              CONFIG              CONFIG
                │                   │                   │
                ▼                   ▼                   ▼
             SOURCE              SOURCE              SOURCE
                │                   │                   │
                ▼                   ▼                   ▼
             CONTENT             CONTENT             CONTENT
                │                   │                   │
                ▼                   ▼                   ▼
             WEBSITE             WEBSITE             WEBSITE
                │                   │                   │
                ▼                   ▼                   ▼
             GITHUB               GITHUB               GITHUB
             PAGES                PAGES                PAGES
```

The **brain is shared**.

The **pipeline is shared**.

The **configuration is different**.

The **content is different**.

The **generated website is independent**.

Adding a new Ustaz should eventually feel like:

```zsh
./archive.zsh create new_ustaz
```

configure the sources, then:

```zsh
./archive.zsh run new_ustaz
```

rather than starting another software project.
