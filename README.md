# micSync

`micSync` is an auto importer for DJI Mic internal recordings.

It is designed for a simple workflow: plug in a DJI Mic receiver or transmitter, let a macOS Shortcut trigger the importer, and get verified local copies under your Downloads folder without manually browsing the device.

## Scope

`micSync` focuses on DJI Mic style WAV files such as:

```text
TX02_MIC001_20260608_112048_orig.wav
TX02_MIC001_20260608_112048_edit.wav
TX00_MIC014_20260608_112048.wav
```

It does four things:

- scans mounted devices for DJI recording files
- mirrors new files into a durable local `raw/` store without modifying the device
- records import metadata in SQLite
- optionally creates an organized browsing copy tree

The recommended setup is macOS + Shortcuts. The core CLI can also run on Windows or Linux when you pass explicit source paths. Notifications, auto-eject, clipboard, and audio duration probing use macOS system tools when available.

## Storage

Default home:

```text
~/Downloads/micSync/
```

Default structure:

```text
~/Downloads/micSync/
  config/
    micsync.env
  runtime/
    logs/
    run/
  recordings/
    raw/
    organized/
    db/
    tmp/
```

`raw/` is the local source of truth. `organized/` is disposable and can be regenerated from `raw/` plus the SQLite catalog.

By default, organized outputs use the `timeline` layout:

```text
recordings/organized/timeline/YYYY/MM/DD/YYYYMMDD_HHMMSS_TXNN_MICNNN[_variant].wav
```

You can switch to `dji` layout:

```text
recordings/organized/dji/TX_MIC001_YYYYMMDD_HHMMSS/TXNN_MICNNN_YYYYMMDD_HHMMSS[_variant].wav
```

The layout is controlled globally with `MICSYNC_ORGANIZED_LAYOUT`. Supported values:

- `timeline`: date-first browsing layout, recommended for most people
- `dji`: keeps organized copies closer to the DJI folder shape

Changing the layout does not rewrite `raw/`. To switch layouts, remove or archive `recordings/organized/`, change `MICSYNC_ORGANIZED_LAYOUT`, then rerun derivation/import so organized copies are recreated.

## Disk Usage

`micSync` always makes a raw mirror, so expect at least one local copy of every imported recording.

Organized outputs add a second visible file tree:

- On macOS with APFS, `MICSYNC_DERIVED_OUTPUTS_STRATEGY=auto` uses clone-on-write copies when available. Finder and simple directory size tools may show roughly double usage, but APFS stores shared extents until one copy changes.
- On non-APFS filesystems, Windows, and Linux, `auto` falls back to ordinary copies. Organized outputs then consume real extra disk space.
- Set `MICSYNC_ENABLE_DERIVED_OUTPUTS=false` if you only want the raw mirror and metadata database.

## Configuration

The wrapper reads configuration from:

```text
$MICSYNC_HOME/config/micsync.env
```

If `MICSYNC_HOME` is not set, it defaults to:

```text
~/Downloads/micSync
```

Common settings:

```dotenv
MICSYNC_HOME=$HOME/Downloads/micSync
MICSYNC_RUNTIME_ROOT=$MICSYNC_HOME/runtime
MICSYNC_RECORDINGS_ROOT=$MICSYNC_HOME/recordings
MICSYNC_RECORDINGS_DB_PATH=$MICSYNC_HOME/recordings/db/recordings.sqlite3
MICSYNC_EXTENSION_ALLOWLIST=.wav
MICSYNC_ENABLE_DERIVED_OUTPUTS=true
MICSYNC_DERIVED_OUTPUTS_STRATEGY=auto
MICSYNC_ORGANIZED_LAYOUT=timeline
MICSYNC_SEGMENT_CADENCE_SECONDS=1800
MICSYNC_SEGMENT_GROUP_TOLERANCE_MS=1000
MICSYNC_NOTIFY=true
MICSYNC_EJECT=true
```

Runtime flags can override common settings for a single run:

```bash
./scripts/micSync.sh --derived false --notify false --eject false
```

## Running

Install/use with Python 3.11 or newer. During development, run from the checkout:

```bash
PYTHONPATH=src python3.11 -m micsync.cli --help
```

Or with `uv`:

```bash
PYTHONPATH=src uv run --python 3.11 python -m micsync.cli --help
```

The wrapper is the recommended entrypoint:

```bash
./scripts/micSync.sh
```

Normal wrapper runs detach into the background and return immediately. That is intentional for Shortcuts, because Shortcut actions should not have to wait for a full import.

Useful examples:

```bash
./scripts/micSync.sh --source-volume "/Volumes/MIC 01"
./scripts/micSync.sh --source-volume "/Volumes/MIC 01" --source-volume "/Volumes/MIC 02"
./scripts/micSync.sh --derived false
./scripts/micSync.sh --stop
```

Foreground bounded validation:

```bash
MICSYNC_HOME=/tmp/micsync-live-test \
PYTHONPATH=src \
python3.11 -m micsync.cli \
  --source-volume "/Volumes/MIC 01" \
  --max-file-size-mb 10 \
  --notify false \
  --eject false
```

The bounded validation contract is:

- source volumes are read-only
- destination is disposable
- file size is capped so you do not accidentally ingest a full device during testing

## Shortcuts Integration

Recommended macOS Shortcut shape:

1. Trigger: external drive connected.
2. Action: run shell script.
3. Shell: `/bin/zsh`.
4. Script:

```bash
cd /path/to/micSync
./scripts/micSync.sh
```

If your Shortcut can pass a mounted volume path, scope the run explicitly:

```bash
cd /path/to/micSync
./scripts/micSync.sh --source-volume "$1"
```

Explicit `--source-volume` is optional on macOS because `micSync` can scan mounted volumes, but it is useful when permissions or timing around a newly mounted device are inconsistent.

Concurrent Shortcut triggers collapse into one active run through a lock/rescan mechanism. When a run starts, `micSync` tries to copy the exact stop command to the clipboard and includes the stop hint in the notification.

## Platform Notes

macOS provides the most automated workflow.

- Shortcuts automation is macOS-specific.
- Notifications use `osascript`.
- Clipboard stop-command copy uses `pbcopy`.
- Auto-eject uses `diskutil`.
- Duration probing uses `afinfo`.
- APFS clone copies use `cp -c`.

On Windows and Linux, run the CLI with explicit `--source-volume` paths and set `MICSYNC_NOTIFY=false` and `MICSYNC_EJECT=false`. Organized outputs still work, but `auto` uses ordinary file copies.

## Database Model

The catalog uses this shape:

```text
source_files -> segments -> takes
```

- `source_files`: one row per mirrored raw file
- `segments`: one recording chunk, with `_orig` and `_edit` variants grouped together
- `takes`: one logical recording grouped from adjacent 30-minute chunks
- `hidden`: records files found under supported trash paths without showing them in normal organized output

Recording timeline fields use the recorder's local wall-clock capture time. Operational fields use local ISO 8601 timestamps with UTC offsets.

## Testing

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python3.11 -m unittest discover -s tests -p 'test_*.py' -v
```

With `uv`:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
uv run --python 3.11 python -m unittest discover -s tests -p 'test_*.py' -v
```

## Boundaries

- `micSync` is a file importer, not a media manager.
- No source deletion from the DJI device.
- No transcript, tag, UI, or sync layer.
- Organized outputs are optional and rebuildable.
- Windows/Linux support is CLI-oriented and less automated than macOS.
