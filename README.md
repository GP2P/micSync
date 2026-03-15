# micSync

`micSync` is a Magnetar host-side importer for DJI Mic recordings.

## Paths

- Source: `nodes/magnetar/deploy/services/micSync/`
- Deploy: `$NEXUS_DEPLOY_ROOT/services/micSync/`
- Service runtime: `$NEXUS_DATA_ROOT/micSync/`
- Shared recordings root: `$NEXUS_DATA_ROOT/recordings/`

## Runtime Config

Tracked template:

- Source: `nodes/magnetar/deploy/services/micSync/.env.template`

Local runtime file:

- Deploy: `$NEXUS_DATA_ROOT/micSync/config/micsync.env`

## Shortcut Command

Use this wrapper from macOS Shortcuts:

```bash
$NEXUS_DEPLOY_ROOT/scripts/micsync-import.sh
```

The wrapper expects `~/.config/nexus/env.sh` to define `NEXUS_DEPLOY_ROOT` and
`NEXUS_DATA_ROOT`.
