## Why

A cascading chain of eight interconnected bugs renders the backup pipeline non-functional and silently accepts data loss. The core issues: (1) stale snapshot state entries after blockcommit cause virsh failures that short-circuit ALL subsequent blockcommits, (2) `qemu-img rebase -u` is missing the `-F qcow2` flag required by QEMU 6.1+, breaking every incremental backup, (3) NBD backup jobs are never terminated (`virsh domjobabort` missing), holding the VM state change lock and blocking `virsh snapshot-create-as` with a 180s timeout, (4) FULL backups are created, rebased against, and cascade-deleted with zero integrity verification — a corrupt FULL silently propagates and eventually destroys all dependent incrementals. The system currently has no guardrails at any point in the FULL backup lifecycle.

This fix targets every point in the failure chain simultaneously: stale state detection, rebase correctness, NBD job lifecycle, mandatory M1 verification at three critical points (post-create, pre-rebase, pre-deletion), snapshot lock-conflict retry, and partial rsync file cleanup.

## What Changes

- **Fix `qemu-img rebase -u`**: Add `-F qcow2` flag at both rebase sites in `qsnap/modules/backup/file_copy.py` (lines 208–215 and 251–258). QEMU 6.1+ requires `-F` (backing format) when `-u` (unsafe/quick rebase) is used with `-b` (new backing file).
- **Add `virsh domjobabort` NBD cleanup**: After `qemu-img convert` from NBD in `nbd_full_export()` (and its callers), explicitly abort the `virsh backup-begin` job with `virsh domjobabort --domain <vm>`. Prevent orphaned backup jobs from holding the state change lock across pipeline runs.
- **Stale state guards for blockcommit and rsync**: Before executing `virsh blockcommit --top SNAP_PATH`, verify `os.path.exists(SNAP_PATH)` — if the file is gone (already committed), call `remove_snapshot()` and skip. Before `rsync` of a snapshot, same check. Eliminates the short-circuit bug where one stale entry blocks all subsequent blockcommits.
- **FULL backup mandatary verification (M1)**: After `create_full_backup()`, run `qemu-img info --output=json` on the resulting FULL to verify it is a valid qcow2 with no corrupt bit. On failure, delete the file and do NOT record it in state. Before `qemu-img rebase -u` to a FULL anchor, re-verify the FULL (M1 — reads only the header). Before cascade-deletion of a FULL and its dependents, re-verify the FULL; if corrupt, BLOCK the entire cascade-delete and log CRITICAL. This M1 gate at the deletion point MUST NOT be configurable — it is always enforced.
- **Optional structural verification (M2)**: Configurable `qemu-img check --output=json` at post-create and pre-deletion. Enabled by default for post-create, recommended for pre-deletion. Faster than M3, catches refcount/L1/L2 corruption.
- **Optional hash verification (M3)**: SHA-256 comparison between FULL and source snapshot at post-create. Disabled by default (full disk read cost). Configurable via `full_verify_after_create = "hash"`.
- **Snapshot creation retry on lock conflict**: `ExternalSnapshotProvider.create()` retries `virsh snapshot-create-as` up to 3 times with exponential backoff (2s, 4s, 8s) when the error indicates a state change lock conflict.
- **New config fields in `[global]`**: `full_verify_after_create` (default `"check"` — M1 + M2), `full_verify_before_rebase` (default `"metadata"` — M1 always, non-configurable minimum), `full_verify_before_delete` (minimum `"metadata"` — M1 enforced regardless of config, `"check"` optional).
- **Partial rsync file cleanup**: `_preflight_cleanup()` additionally detects truncated `.qcow2` files on backup targets (files where `qemu-img info` fails) and deletes them as stale.

## Capabilities

### New Capabilities
- `backup-full-verification`: Mandatory and optional integrity verification of FULL backup files at three lifecycle points — post-creation (before state recording), pre-rebase (before linking incrementals), and pre-deletion (before cascade-delete). Covers M1 (qemu-img info header check + corrupt-bit detection), M2 (qemu-img check structural scan), and M3 (SHA-256 content hash comparison) verification tiers.

### Modified Capabilities
- `backup-provider`: Add `-F qcow2` to `qemu-img rebase -u` commands; add M1 pre-rebase FULL integrity check in `transfer_missing()`; add M1/M2 post-create verification in `create_full_backup()`; add `virsh domjobabort` NBD job cleanup.
- `live-vm-full-backup`: Require `virsh domjobabort --domain <vm>` in NBD cleanup path (`nbd_helper.py` finally block) to terminate the `virsh backup-begin` job after `qemu-img convert` completes or fails.
- `nbd-bitmap-backup`: Same `virsh domjobabort` fix applies via shared `nbd_full_export()` helper.
- `cascade-deletion`: Add mandatory M1 integrity gate — before cascade-deleting a FULL and its dependent incrementals, verify the FULL passes `qemu-img info` with no corrupt bit. On failure, block the entire cascade-delete and log CRITICAL.
- `snapshot-provider`: Add lock-conflict retry in `ExternalSnapshotProvider.create()` — detect state change lock errors and retry with exponential backoff (3 attempts, 2s/4s/8s).
- `pre-flight-cleanup`: Extend stale file detection to include truncated `.qcow2` files on backup targets (files where `qemu-img info` fails), deleting them before pipeline execution.
- `chain-integrity-verification`: Add `os.path.exists()` guard in `_blockcommit_snapshots()` — if a snapshot file in `to_merge` does not exist, call `remove_snapshot()` and skip the blockcommit for that entry. Prevent single stale entry from short-circuiting all subsequent blockcommits.
- `config-model`: Add `full_verify_after_create` (default `"check"`), `full_verify_before_rebase` (default `"metadata"`), `full_verify_before_delete` (default `"check"`), and `deep_check_targets` (default `false`) fields to `GlobalConfig`.
- `core-orchestrator`: Pipeline changes — call `verify_backup()` after `create_full_backup()`, gate `_cleanup_backups()` on M1 FULL integrity, add stale-state self-healing before blockcommit.
- `backup-verification`: Extend `verify_backup()` to accept a single-path mode (no source comparison needed) for M1 verification of standalone FULL files.

## Impact

| Area | Impact |
|---|---|
| `qsnap/modules/backup/file_copy.py` | Fix `-F qcow2` in 2 rebase sites; add `verify_backup()` call in `create_full_backup()`; add M1 pre-rebase check in `transfer_missing()` |
| `qsnap/modules/backup/nbd_helper.py` | Add `virsh domjobabort --domain <vm>` in `finally` block |
| `qsnap/modules/backup/verification.py` | Add single-path mode for FULL-only verification (no source comparison) |
| `qsnap/modules/snapshot/external.py` | Add retry loop for lock-conflict on `virsh snapshot-create-as` |
| `qsnap/core/__init__.py` | `_blockcommit_snapshots()` — add `os.path.exists()` guard + `remove_snapshot()` for stale entries; `_cleanup_backups()` — add M1 FULL gate before cascade-delete; `_backup_target()` — call `verify_backup()` after FULL creation; `_preflight_cleanup()` — add truncated qcow2 detection |
| `qsnap/models/config.py` | Add `full_verify_after_create`, `full_verify_before_rebase`, `full_verify_before_delete`, `deep_check_targets` fields to `GlobalConfig` |
| `qsnap/config/facade.py` | Parse new config fields (TOML → frozen dataclass); add bucket validation (targets require at least one active retention bucket) |
| `qsnap/interfaces/state.py` | Add `remove_full_backup()` and `remove_incremental_dependency()` abstract methods to `IStateManager` |
| `qsnap/models/results.py` | Add `StateCheckResult` dataclass for `qsnap check --state` output |
| `qsnap/cli/commands.py` | Wire `qsnap check --state` CLI command |
| `qsnap/cli/app.py` | Add `--state` flag to check subcommand |
| **ABC interface changes** | `IStateManager` gains 2 new methods: `remove_full_backup()`, `remove_incremental_dependency()`. `verify_full_backup()` signature changes: `expected_hash` parameter replaced with `source_path` for M3 qemu-img compare. |

## Phase 2 — State Integrity Fixes (Post-verification Amendments)

During verification, two critical issues and four additional improvements were identified:

1. **D4 path creates unverified FULLs** — `transfer_missing()` bypassed Core's verification pipeline. Removed the D4 code path; added config validation ensuring active retention buckets when targets are configured.
2. **M3 SHA-256 always fails for NBD-FULL** — SHA-256 of snapshot delta ≠ SHA-256 of standalone FULL. Replaced with `qemu-img compare --force-share` which compares virtual-disk content (traverses backing chain).
3. **Phantom FULLs accumulate in state** — Added `remove_full_backup()` to `IStateManager`; called when FULL is deleted. Added `os.path.exists()` guard for FULLs in `get_full_backups()`.
4. **Orphaned dependencies not cleaned** — Added `remove_incremental_dependency()` to `IStateManager`.
5. **Config without active buckets silently creates no FULLs** — Added `ConfigError` validation.
6. **No tool to audit state consistency** — Added `qsnap check --state` command detecting phantom entries and corrupted state files.
| **IStateManager schema unchanged** | `remove_snapshot()` already exists in the ABC (added in commit `acde50c`). No new state methods needed. |
