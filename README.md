# micSync

`micSync` is a Magnetar host-side importer for DJI Mic recordings.

## Paths

- Source: `nodes/magnetar/deploy/services/micSync/`
- Deploy: `$NEXUS_DEPLOY_ROOT/services/micSync/`
- Service runtime: `$NEXUS_DATA_ROOT/micSync/`
- Shared audio recordings root: `$NEXUS_DATA_ROOT/recordings/audio/`

## Runtime Config

Tracked template:

- Source: `nodes/magnetar/deploy/services/micSync/.env.template`

Local runtime file:

- Deploy: `$NEXUS_DATA_ROOT/micSync/config/micsync.env`

Important grouping settings:

- `MICSYNC_SEGMENT_CADENCE_SECONDS=1800`
- `MICSYNC_SEGMENT_GROUP_TOLERANCE_MS=1000`
- `MICSYNC_ENABLE_DERIVED_OUTPUTS=false`
- `MICSYNC_DERIVED_OUTPUTS_STRATEGY=clone_then_copy`

These control when adjacent DJI chunks collapse into the same take in the shared recordings DB and whether optional `derived/` browse outputs should be created.

## Shortcut Command

Use this wrapper from macOS Shortcuts:

```bash
$NEXUS_DEPLOY_ROOT/scripts/micsync-import.sh
```

The wrapper expects `~/.config/nexus/env.sh` to define `NEXUS_DEPLOY_ROOT` and
`NEXUS_DATA_ROOT`.
