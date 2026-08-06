# Design: sync-spec-drift

## D1 — Spec-only change

Every item documents behavior that already exists in code and is already tested. No code or test changes. Verification = `openspec validate --strict` + archive sync + full spec validation.

## D2 — bucket_level → disk in cascade-deletion

`FullBackupInfo.disk` and `record_full_backup(target_path, name, timestamp, disk)` replaced the obsolete bucket-driven model when per-chain retention landed. The requirement is re-issued with the same name via the RENAMED(superseded) → REMOVED → ADDED pattern because the scenario "FULL recorded with bucket level" must change its name to "FULL recorded for a disk", which MODIFIED cannot express.

## D3 — Two-layer resolution contract (restore-command ↔ fork-mode)

`_resolve_snapshot()` is the shared primitive: it raises `FileNotFoundError("Snapshot not found: {name}")`. `restore()` and `fork()` are the public commands: they catch it and return `RestoreResult(success=False, error="Snapshot not found: {name}")` — "never raise for expected failures" (Result-object convention). The specs now state both layers explicitly so the reader sees no contradiction.

## D4 — stats is a CLI composition, not a Core method

`handle_stats` composes `core.list_snapshots()` + `core.list_backups()`; there is no `Core.stats()`. The requirement is written against the CLI command with the two Core methods named as data sources. Sizes are sums of `SnapshotInfo.allocation`; VM scope is the union of the two config-driven listings (i.e. only VMs present in TOML).
