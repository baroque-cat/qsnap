## Context

qsnap completed a multi-disk refactor: `VMConfig.disks: list[DiskConfig]`, per-disk state (`last_allocation` dict keyed by disk, `SnapshotInfo.disk`, `FullBackupInfo.disk`, deferred ops with `disk`), per-disk snapshot naming `{vm}.{ts}_{disk}_{6hex}.qcow2`, and per-disk FULL naming `{vm}.FULL.{ts}_{disk}_{6hex}.qcow2`. Modules (`ISnapshotProvider`, `ILifecycleManager`, `IBackupProvider`) are per-disk by construction.

Five defects remain where disks are mixed or reliability guarantees are missing (see proposal). The architecture is DI-with-ABC per AGENTS.md: Core is the sole coordinator, modules are stateless workers implementing exactly one ABC, expected failures return result objects, all external commands go through `IShell`, and stateless utilities (precedent: `scan_backing_chain()` in `utils/verification.py`, shared by Core and modules) are an established pattern.

Key verified facts constraining this design:

- `triple-source-check/spec.md` already requires per-disk active-layer matching — F1 is a code-vs-spec violation.
- Checkpoint names are `qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6hex}` (`BitmapBackupProvider._new_checkpoint_name`) — the disk segment enables per-disk filtering.
- `parse_disk_from_snapshot_name` regex `\d{8}T\d{6}_([^_]+)_[0-9a-fA-F]{6}` matches both snapshot and FULL names.
- Transaction log emits a btrbk-compatible 6-field line (`localtime type status target_url source_url parent_url`) — format is an external compatibility contract.
- `verify_full_backup()` (utils/verification.py) implements M1 (metadata/virtual-size) / M2 (`qemu-img check`) / M3 (compare) tiers — the verification approach to reuse.
- Pure retry utilities exist (`is_retryable`, `compute_backoff`, `parse_retry_duration` in utils/retry.py) with limits from `GlobalConfig.backup_retry_max` / `backup_retry_base`.

## Goals / Non-Goals

**Goals**

- Every operation on one disk reads and writes ONLY that disk's data: chain, state, checkpoints, verification.
- `fork` and `restore` meet the same reliability bar as the pipeline (verify, retry, no partial litter).
- `fork --dry-run` previews instead of converting.
- Audit trail and summary distinguish disks.

**Non-Goals**

- Dry-run prediction quality in the main pipeline (stale-state retention, missing incremental-transfer prediction) — separate change.
- Multi-disk "restore all disks at timestamp T" mode — out of scope; per-disk restore stays the unit.
- VM discovery beyond TOML config for list/fork/restore — unchanged (`_filter_vms` behavior preserved).
- Deleting orphaned backup FILES after restore — files stay; only their state records are cleared (they surface as orphans; deletion without verification is forbidden by design D3 verify-before-delete).

## Decisions

### D1 — F1: per-disk grouping inside the existing check (no new module)

`_verify_active_layer_match` builds `newest_by_disk: dict[str, SnapshotInfo]` (max timestamp per `snap.disk`), then for each `(target_dev, source_path)` from `parse_domblklist_path_map`: if `target_dev` has no snapshots, skip (nothing to compare); else compare `source_path` with `str(newest_by_disk[target_dev].path)` and include the disk name in the mismatch message.

**Alternatives considered:** (a) a new `IActiveLayerVerifier` module — rejected: the check is part of `Core.check()`'s triple-source orchestration; adding an ABC + factory branch for one internal check is overengineering. (b) fixing the spec instead — rejected: the spec is already correct per-disk; the code is wrong.

### D2 — F2: `disk` flows through result objects; btrbk log format untouched

`ActionRecord` and `BackupResult` gain `disk: str | None = None` (default keeps construction sites that legitimately have no disk, e.g. VM-level `error` records). `BitmapBackupProvider.transfer_missing` sets `disk=snapshot.disk` on every result; `create_full_backup` already knows its source snapshot's disk. Summary renders `[disk]` after the action symbol only when `disk is not None`. Transaction log line format is NOT extended — it is btrbk-compatible and the disk is already encoded in `source_url`/`target_url` paths; the spec is amended to state this explicitly.

**Alternatives considered:** (a) encoding disk only in `name` and parsing it downstream — rejected: consumers would re-implement `parse_disk_from_snapshot_name` and break on legacy names. (b) adding a 7th transaction-log field — rejected: breaks btrbk tooling compatibility.

### D3 — F3: fork dry-run mirrors the restore/reconcile pattern

Local `--dry-run` flag on the fork subparser (`default=argparse.SUPPRESS`, same as reconcile) plus the existing global `-n` (already assigned to `core.dry_run` at `cli/app.py:290`). In `Core.fork()`, the gate sits AFTER the read-only chain-size estimate (`qemu-img info --backing-chain --force-share` is non-mutating and safe in dry-run): log `[dry-run] Would convert <source> (chain size ~X) -> <output>` and return `RestoreResult(success=True)` without creating any file.

**Alternatives considered:** a separate preview code path — rejected: the project pattern is one code path with guarded mutations (run/restore/reconcile all do this).

### D4 — F4: surgical per-disk state resets via two new `IStateManager` methods

New abstract methods (existing full-reset methods remain — they are spec'd and used elsewhere):

- `reset_vm_disk_state(vm_name, disk)` — removes snapshots where `snap.disk == disk`; removes key `disk` from `last_allocation` (legacy bare-int values become `None`); removes deferred ops where `op.disk == disk`.
- `reset_target_disk_state(target_path, vm_name, disk)` — in `_full_backups.json`, removes entries whose name starts with `{vm_name}.` AND whose disk equals `disk` (other VMs sharing the target are untouched — this also fixes the hidden cross-VM wipe); in `_dependencies.json`, removes FULL keys belonging to `(vm, disk)` (disk extracted from the FULL name via `parse_disk_from_snapshot_name`); in `_target_state.json`, removes `last_backup_allocation[disk]`.

Restore step 8 calls these instead of the full resets. Step 9 (`_cleanup_checkpoints_after_restore`) gains a `disk` parameter and deletes only checkpoints whose name's 3rd `-`-separated segment equals the disk (format `qsnap-{hash}-{disk}-{ts}-{hex}` is fixed by `_new_checkpoint_name`). Legacy checkpoint names without a disk segment (5 parts) are NOT deleted — WARNING only (conservative: we cannot prove ownership).

**Alternatives considered:** (a) keeping full resets but scoping restore to a dedicated target — rejected: does not fix multi-disk VMs and is a config-level workaround for a code bug. (b) deleting other disks' checkpoints too — rejected: destroys their incremental bitmap lines. (c) auto-deleting orphaned backup files of the restored disk — rejected: violates verify-before-delete; files remain discoverable as orphans.

### D5 — F5: shared stateless convert utility, not a new ABC module

New `qsnap/utils/convert.py` (stateless, `IShell` passed as parameter, result objects returned):

- `convert_to_standalone(shell, source, output, timeout=7200) -> ShellResult` — `qemu-img convert --force-share -O qcow2`; on failure, best-effort remove the partial `output`.
- `verify_standalone_image(shell, source, output) -> str | None` — M1: `virtual-size(output) == virtual-size(source)` via `qemu-img info --force-share`; M2: `qemu-img check output` clean. Returns error string or `None` (same convention as `verify_full_backup`).
- `convert_with_retry(shell, source, output, retry_max, retry_base) -> ShellResult` — wraps convert with `is_retryable`/`compute_backoff` from utils/retry.py; partial file removed before each retry.

`Core.fork()`: convert → verify → on verify failure remove output and return error result. `Core.restore()`: convert to tmp → **verify tmp BEFORE `os.replace(tmp, base_image)`** — a corrupt image never becomes the base. Retry limits reuse `GlobalConfig.backup_retry_max` / `backup_retry_base` (user-approved; no new config options).

**Alternatives considered:** (a) `IImageConverter` ABC + factory branch — rejected: one consumer pair, and AGENTS.md factory branches are for pipeline strategies; utilities shared by Core and modules are an established pattern (`scan_backing_chain`). (b) M3 compare verification — rejected: source may be a live chain of a running VM; compare is unsafe/meaningless there. (c) verify after `os.replace` — rejected: replacement is irreversible; verify-first keeps the failure mode recoverable.

## Risks / Trade-offs

- [ABC breakage: `IStateManager` +2 methods, `IBackupProvider` result contract] → All implementations (`JsonStateManager`, `BitmapBackupProvider`) and all mocks (`InMemoryStateManager`, `MockBackupProvider`, `MockBitmapBackupProvider`) plus contract tests update in the same change; mock-parity tests enforce behavioral equivalence.
- [Checkpoint name parsing by `-` segments is fragile if the format ever changes] → Format is fixed by `_new_checkpoint_name`; unknown/legacy shapes are conservatively skipped with WARNING, never deleted.
- [Verification adds time to restore] → M1+M2 are metadata/structure checks — seconds versus the convert duration.
- [Restore behavior change on shared targets (other VMs keep state)] → This is the intended fix; delta specs document the new semantics; no data migration needed.
- [Retrying a multi-GB convert repeats large writes] → Only `is_retryable` errors retry; partial output removed before each attempt; attempt count capped by `backup_retry_max`.
- [`BackupResult.disk` default `None` could hide a missing population site] → Contract tests assert `disk` is populated for `BitmapBackupProvider` results; summary renders missing disk as no prefix (visible regression surface in tests).

## Migration Plan

1. Land model + state + provider changes together (single atomic step for the ABC break), then Core/CLI, then specs sync.
2. No state-file schema migration: new reset methods operate on existing JSON keys; legacy bare-int `last_allocation` treated as absent.
3. Rollback: revert the change; state files remain fully compatible (nothing new is persisted).

## Open Questions

None — user approved: orphaned backup files remain after restore (surfaced via `list backups --tree` orphan grouping), and conversion retries reuse `backup_retry_max` / `backup_retry_base`.
