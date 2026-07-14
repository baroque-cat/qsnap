## Why

qsnap's core snapshot/backup/retention/blockcommit loop is proven, but five gaps prevent production-grade reliability and user confidence: (1) `preserve_min` is hardcoded to `"0h"` with no user-facing config key—snapshots fall out of retention windows immediately, risking data loss before backup transfer; (2) users configure retention policies blindly with no visibility into expected chain length, storage impact, or which snapshots will be removed; (3) `verify="metadata"` catches header corruption but not bit rot or silent `cp` errors; (4) incremental backup chains on target are fragile—one corrupted file breaks all subsequent backups with no anchor recovery point; (5) the sparse README lacks guidance for production deployment scenarios.

## What Changes

- **`preserve_min` as a first-class config option** — new `snapshot_preserve_min` and `target_preserve_min` keys at global, VM, and target levels with full option inheritance (global → VM → target). Replaces the hardcoded `"0h"` default.
- **Schedule summary calculator** — new `Core.schedule_summary()` method that simulates the retention engine against a realistic timestamp distribution and outputs expected chain length, bucket breakdown, and storage estimate. Printed at every timer invocation (INFO log) and on `--print-schedule`.
- **`verify="hash"` backup verification tier** — new mid-level between `"metadata"` (fast, shallow) and `"full"` (slow, byte-level). Computes SHA-256 of the snapshot file at creation time, stores it in `SnapshotInfo`, and validates the hash on the target after transfer. Catches bit rot and copy errors in seconds.
- **Periodic full (anchor) backups** — new `full_every` target-level option with optional `full_compress`. Creates standalone qcow2 files on the target via `qemu-img convert` at a configurable interval (e.g., every 7 days). Subsequent incrementals rebase to the anchor, making them independent of the previous fragile chain.
- **Improved README** — comprehensive quick-start, full configuration reference with all new keys, retention strategy examples, verification guidance, and deployment scenario templates (home host + USB, server + network target).

## Capabilities

### New Capabilities

- `preserve-min-config`: Separate `snapshot_preserve_min` and `target_preserve_min` configuration keys with global→VM→target inheritance.
- `schedule-summary`: Simulated retention evaluation that shows expected chain length, bucket breakdown, and storage estimates before any snapshots are created or removed.
- `backup-hash-verification`: Binary SHA-256 verification of backup files at the `verify="hash"` tier.
- `periodic-full-backup`: Anchor full backups via `qemu-img convert` on the target at configurable intervals, with optional zlib compression.

### Modified Capabilities

- `config-model`: `GlobalConfig`, `VMConfig`, and `TargetConfig` gain `snapshot_preserve_min` / `target_preserve_min` fields. `TargetConfig` gains `full_every` and `full_compress` fields. `SnapshotResult` and `SnapshotInfo` gain `content_hash` field.
- `backup-verification`: `verify_backup()` accepts `expected_hash` parameter and supports `"hash"` mode.
- `backup-provider`: `FileCopyBackupProvider` gains `create_full_backup()` method and `transfer_missing()` rebases to the FULL anchor when one exists.
- `state-management`: `IStateManager` gains `get_last_full_backup()` / `set_last_full_backup()` for tracking anchor backup cadence.
- `core-orchestrator`: `Core._backup_target()` runs full backup check before incremental transfer. `Core._parse_preserve()` accepts separate `preserve_min` parameter. New `Core.schedule_summary()` method.
- `cli-interface`: `--print-schedule` / `-S` flag outputs the schedule summary. Timer invocation logs summary at INFO level.

## Impact

- **Source**: `qsnap/models/config.py` + `qsnap/models/results.py` (new fields in 4 dataclasses), `qsnap/config/facade.py` (parse new TOML keys with inheritance), `qsnap/core/__init__.py` (schedule_summary, full backup wiring, updated _parse_preserve), `qsnap/modules/snapshot/external.py` (SHA-256 computation), `qsnap/modules/backup/verification.py` (verify="hash" and _file_sha256), `qsnap/modules/backup/file_copy.py` (create_full_backup, update transfer_missing), `qsnap/interfaces/state.py` (get/set last_full_backup), `qsnap/state/json_manager.py` (persist full backup metadata), `qsnap/retention/time_based.py` (explain method for schedule summary), `qsnap/cli/commands.py` (schedule summary output).
- **No breaking changes** — all new config keys have defaults preserving current behavior (`preserve_min=None` → `"0h"`, `full_every="0d"` → disabled, `verify` unchanged).
- **No new dependencies** — only stdlib `hashlib` (already imported by `MapChangeDetector`).
