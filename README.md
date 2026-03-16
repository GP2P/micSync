# micSync

`micSync` is a host-side importer for autonomously importing DJI Mic internal recordings in the background.

It is a series of python scripts designed for a Mac host that receives Apple Shortcut styled "drive connected" triggers, mirrors new source files into a canonical local raw store, derives a recording database from that raw mirror, and optionally creates normalized browse copies in a separate derived tree without taking up additional space.

## Status

The current design assumes:

- macOS host runtime
- DJI Mic 3 styled recording names such as `TX02_MIC002_20230820_235500_orig.wav`
- source-first ingestion into a local raw mirror
- SQLite metadata catalog
- optional derived outputs that are not required for DB integrity

## Features

- Mirrors source recordings into a local `raw/` tree without modifying the device
- Groups `_orig` and `_edit` source files into the same `segment`
- Groups adjacent 30-minute segments into the same `take` when timing is continuous enough
- Handles duplicate Shortcut triggers and frozen processes with a singleton lock and rescan marker
- Supports resumable runs and duplicate detection
- Sends macOS notifications
- Can auto-eject source volumes after a clean verified mirror stage
- Can optionally create normalized browse copies under `derived/`

## Storage Model

Service-local runtime state:

```text
$NEXUS_DATA_ROOT/micSync/
  config/
  logs/
  run/
```

Shared audio recording corpus:

```text
$NEXUS_DATA_ROOT/recordings/audio/
  raw/
  db/
  derived/
```

Canonical layers:

- `raw/`: mirrored original files copied from the device
- `db/`: SQLite metadata catalog

Optional layer:

- `derived/`: normalized or future generated outputs, disposable and rebuildable, uses APFS clone to duplicate files without causing extra storage overhead

## Database Model

The current canonical model is:

```text
source_files -> segments -> takes
```

- `source_files`: one row per mirrored raw file
- `segments`: one recording chunk, can have one or more variants like `_orig` and `_edit`
- `takes`: one logical grouped recording for timeline and browsing, since DJI Mic automatically cuts recordings into 30min chunks

Future enrichment tables such as transcripts, summaries, or tags should attach to the right canonical level:

- `source_file_id` when provenance is variant-specific
- `segment_id` when the enrichment describes the physical chunk
- `take_id` when it describes the grouped logical recording

## How It Works

Each run has two stages.

1. Mirror stage
   `micSync` scans mounted volumes, copies matching recordings into `raw/`, verifies the copy, records a `source_files` row, and can eject the device after a clean run.

2. Derive stage
   `micSync` reads the local mirrored raw files, parses timestamps and variants, updates `segments` and `takes`, and optionally generates normalized files under `derived/`.

The important boundary is that the raw mirror becomes the local system of record. Once a file has been mirrored and verified, the database and optional derived outputs can be rebuilt without needing the mic to remain connected.

## Naming Rules

Supported source pattern:

```text
TXNN_MICNNN_YYYYMMDD_HHMMSS[_orig|_edit].wav
```

Examples:

- `TX02_MIC001_20260608_112048_orig.wav`
- `TX02_MIC001_20260608_112048_edit.wav`
- `TX00_MIC014_20260608_112048.wav`

When derived outputs are enabled, the normalized path format is:

```text
derived/normalized/YYYY/MM/DD/YYYYMMDD_HHMMSS_TXNN_MICNNN[_variant].wav
```

## Configuration

Tracked template:

- `.env.template`

Runtime config file:

- Deploy: `$NEXUS_DATA_ROOT/micSync/config/micsync.env`

Important variables:

```dotenv
MICSYNC_RUNTIME_ROOT=$NEXUS_DATA_ROOT/micSync
MICSYNC_RECORDINGS_ROOT=$NEXUS_DATA_ROOT/recordings/audio
MICSYNC_RECORDINGS_DB_PATH=$NEXUS_DATA_ROOT/recordings/audio/db/recordings.sqlite3
MICSYNC_EXTENSION_ALLOWLIST=.wav
MICSYNC_ENABLE_DERIVED_OUTPUTS=false
MICSYNC_DERIVED_OUTPUTS_STRATEGY=clone_then_copy
MICSYNC_SEGMENT_CADENCE_SECONDS=1800
MICSYNC_SEGMENT_GROUP_TOLERANCE_MS=1000
MICSYNC_NOTIFY=true
MICSYNC_EJECT=true
```
Note: `NEXUS_DATA_ROOT` can point at any parent directory where you want `micSync/` and `recordings/` created.

Notes:

- `MICSYNC_ENABLE_DERIVED_OUTPUTS=false` keeps the system in `raw + db` mode only.
- `MICSYNC_DERIVED_OUTPUTS_STRATEGY=clone_then_copy` prefers APFS clone-on-write on macOS and falls back to ordinary copy.
- `MICSYNC_SEGMENT_GROUP_TOLERANCE_MS=1000` exists because real DJI 30-minute chunks are not perfectly exact at the millisecond level.

## Running

Standalone wrapper script:

```bash
./scripts/micsync.sh
```

It sets these environment variables for the process:

- `NEXUS_DEPLOY_ROOT`
- `NEXUS_DATA_ROOT`

Defaults:

- `NEXUS_DEPLOY_ROOT=<service root inferred from ./scripts/micsync.sh>`
- `NEXUS_DATA_ROOT=$NEXUS_DEPLOY_ROOT/data`

Example bounded local run with explicit roots:

```bash
NEXUS_DEPLOY_ROOT="$PWD" \
NEXUS_DATA_ROOT=/tmp/micsync-test \
./scripts/micsync.sh \
  --max-file-size-mb 10 \
  --notify false \
  --eject false
```

Example using the built-in defaults:

```bash
./scripts/micsync.sh --help
```

Graceful stop request:

```bash
./scripts/micsync.sh --stop
```

The wrapper infers its root from its own location on disk, not from the caller's current working directory.

If you are integrating `micSync` into a larger deployment that already sets `NEXUS_DEPLOY_ROOT` and `NEXUS_DATA_ROOT`, this wrapper will respect those existing values.

## Shortcuts Integration

`micSync` is intended to be triggered by macOS Shortcuts when an external drive connects.

Recommended pattern:

- Shortcut calls `./scripts/micsync.sh`
- `micSync` itself scans all mounted volumes
- concurrent triggers collapse into one active run via the lock/rescan mechanism

This means the automation does not need to reliably pass a specific drive path for correctness.

When an import starts, `micSync` copies the exact stop command for that run to the clipboard when possible and includes that fact in the start notification. Running the copied command, or `./scripts/micsync.sh --stop`, requests a graceful stop: the current file finishes first, then the importer skips the remaining work, releases the lock, and sends a stopped notification.

## Testing

Unit tests:

```bash
cd /path/to/micSync
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Bounded live-device validation:

```bash
NEXUS_DEPLOY_ROOT="$PWD" \
NEXUS_DATA_ROOT=/tmp/micsync-live-test \
./scripts/micsync.sh \
  --max-file-size-mb 10 \
  --notify false \
  --eject false
```

The live validation contract is:

- read-only behavior toward source volumes
- disposable local destination
- bounded file-size filter to avoid ingesting an entire device during tests

## Stop Semantics

- `./scripts/micsync.sh --stop` is the preferred way to stop a run
- the importer stops between files and phases, not in the middle of a file copy
- copied files are written through temp paths and `fsync` before promotion, so interrupted runs should not leave corrupted final files
- force-terminating the process is usually recoverable, but it is not the same as a graceful stop because you can lose the final notification, leave temp files behind, and skip a clean lock handoff

## Limitations

- macOS-specific host behavior
- Shortcut-triggered, not background disk-watcher driven
- no source deletion in v1
- no transcript, tag, or UI layers in v1
- `derived/` is optional and not tracked in the DB

## Future Work

- safe delete-from-device workflow after verified mirroring
- transcript and enrichment tables
- timeline/browser UI
- hotspot-aware remote processing controls
- battery-aware import prompts
- more sophisticated derived outputs such as compression or merged exports
