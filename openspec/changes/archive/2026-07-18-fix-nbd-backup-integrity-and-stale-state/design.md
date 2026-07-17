## Context

Eight interconnected bugs were discovered during deep exploration of a real-world production failure. The failure chain was: NBD backup job left running (no `virsh domjobabort`) → held VM state change lock → `virsh snapshot-create-as` timed out with lock conflict → stale snapshot state entries (from pre-`acde50c` bug) triggered `virsh blockcommit` on deleted files → short-circuit blocked ALL subsequent blockcommits → `rsync` failed on non-existent snapshot files → valid incrementals were transferred but `qemu-img rebase -u` failed without `-F qcow2` flag → system exited with `EXIT_BACKUP_ABORT`. Additionally, the codebase had ZERO integrity verification of FULL backups at any lifecycle point — a corrupt FULL silently propagates through the system and eventually cascade-deletes dependent incrementals.

The current architecture (per AGENTS.md): Core orchestrates via `_execute_pipeline()`, modules are stateless workers implementing ABCs, `IStateManager` persists cross-run data, `IShell` wraps all external commands, `IConfigFacade` produces frozen `GlobalConfig`/`VMConfig`/`TargetConfig` dataclasses.

## Goals / Non-Goals

**Goals:**
- Fix all P0 bugs: rebase `-F` flag, NBD `domjobabort`, stale state self-healing, mandatory M1 verification at three FULL lifecycle points
- Fix all P1 bugs: snapshot lock-conflict retry, FULL verification post-create/pre-delete
- Fix P2: partial rsync qcow2 detection in pre-flight cleanup
- Add clean config knobs for verification tiers (M1/M2/M3) with sensible defaults
- Maintain zero ABC interface changes — all fixes are within existing implementations
- Keep modules stateless — verification flows through Core, not stored in module instances

**Non-Goals:**
- Do NOT change the blockcommit execution logic (BlockCommitManager, QemuImgCommitManager) — they work correctly
- Do NOT change `ILifecycleManager`, `ISnapshotProvider`, `IBackupProvider` ABCs
- Do NOT change `IStateManager` ABC — `remove_snapshot()` already exists
- Do NOT alter the pipeline ordering (`_execute_pipeline` step sequence)
- Do NOT add retry to backup transfers (already exists in `_transfer_with_retry`)
- Do NOT change backup target retention logic beyond the pre-deletion integrity gate
- Do NOT implement M3 (hash) verification as default — it is opt-in

## Decisions

### Decision 1: M1 (metadata) verification is hardcoded at pre-deletion — not configurable

**Rationale:** Cascade-deletion is irreversible. If a corrupt FULL is deleted along with its dependent incrementals, data is permanently lost. The cost of M1 (`qemu-img info --output=json`) is ~0.1 seconds — just reading the qcow2 header. There is no scenario where skipping this check is acceptable. Unlike post-create or pre-rebase verification (where a failure just means "try again next run" or "use an alternative anchor"), a pre-deletion failure means "we almost destroyed recoverable data."

**Alternatives considered:**
- **A: Make pre-deletion M1 configurable like post-create.** Rejected — too dangerous. A user disabling it by accident loses data.
- **B: Require M2 (`qemu-img check`) at pre-deletion.** Rejected — `qemu-img check` on large disks takes minutes, bloating the pipeline. M1 catches the critical cases (corrupt bit, unreadable header) at near-zero cost. M2 remains configurable for extra safety.

### Decision 2: `verify_backup()` gains a single-path mode (no source comparison)

**Rationale:** The existing `verify_backup(shell, source_path, target_path, verify_mode, expected_hash=None)` always compares source and target. For FULL backup post-creation verification, there is no meaningful "source" — the source is the running VM's disk exported via NBD, which is not a static file. Instead, we need to verify the standalone FULL file's structural integrity.

We extend `verify_backup()` with `verify_full_backup(shell, target_path, verify_mode, expected_virtual_size=None, expected_hash=None)` — a new function that:
- `"metadata"` (M1): runs `qemu-img info --output=json`, checks format=qcw2, no corrupt bit (incompatible_features bit 1), optional virtual-size match
- `"check"` (M2): additionally runs `qemu-img check --output=json`, checks no errors reported
- `"hash"` (M3): additionally computes SHA-256 and compares to `expected_hash`

**Alternatives considered:**
- **A: Pass `source_path=None` to existing `verify_backup()`.** Rejected — changes the contract semantics of a function used in many places. Cleaner to add a separate function with its own clear purpose.
- **B: Inline the checks in `create_full_backup()`.** Rejected — duplicates logic, harder to test. Verification lives in `verification.py` by design.

### Decision 3: Single `nbd_full_export()` function gets `domjobabort` in its `finally` block

**Rationale:** Both `FileCopyBackupProvider.create_full_backup()` and `BitmapBackupProvider.create_full_backup()` (and `Core.fork()`) use the same `nbd_full_export()` helper from `nbd_helper.py`. Adding `virsh domjobabort --domain <vm>` to the `finally` block fixes all callers at once. The function already has a `finally` block for socket cleanup — we add the abort call there.

**Sequence:**
```python
finally:
    # 1. Abort the backup job (if running) to release state change lock
    shell.run(["virsh", "domjobabort", "--domain", vm_name], timeout=30)
    # 2. Clean up the socket
    shell.run(["rm", "-f", str(socket_path)], timeout=10)
```

The `domjobabort` is fire-and-forget with a short timeout. If it fails (job already terminated, VM stopped, etc.), we log a warning but don't propagate the error — the socket cleanup is the critical path.

**Alternatives considered:**
- **A: Use `virsh backup-end`.** Rejected — `backup-end` requires the backup XML and is designed for graceful completion, not abort scenarios.
- **B: Poll `virsh domjobinfo` before aborting.** Rejected — adds unnecessary latency and complexity. `domjobabort` is idempotent (safe to call when no job exists).
- **C: Add `domjobabort` in the caller (each `create_full_backup()`).** Rejected — duplicates the fix across 3+ call sites. Centralizing in the helper is cleaner.

### Decision 4: Stale state self-healing in `_blockcommit_snapshots()` uses `os.path.exists()` + `remove_snapshot()`

**Rationale:** Before iterating `to_merge` and calling `BlockCommitManager.blockcommit()`, Core checks each snapshot's file path. If the file doesn't exist, the snapshot was already blockcommitted (by a previous run that failed to update state). Core calls `self._state.remove_snapshot()` and removes the entry from `to_merge`. This prevents the short-circuit bug where one stale entry blocks all subsequent blockcommits.

The check is cheap (`os.path.exists()`) and runs before the pre-commit chain verification. If ALL entries in `to_merge` are stale, the entire blockcommit step is skipped. If some are stale and some are real, only the real ones are committed.

**Alternatives considered:**
- **A: Add file-existence check in `BlockCommitManager.blockcommit()`.** Rejected — violates AGENTS.md anti-pattern: modules should not access IStateManager. The stale state concept belongs to Core orchestration, not the lifecycle module.
- **B: Run a full state reconciliation pass before each pipeline.** Rejected — over-engineered. The `os.path.exists()` guard at the point of use is simpler and fixes the immediate problem.

### Decision 5: Snapshot lock-conflict retry with exponential backoff

**Rationale:** `ExternalSnapshotProvider.create()` currently has zero retry logic. When `virsh snapshot-create-as` fails with a state change lock conflict (held by a lingering backup job), the pipeline fails immediately. The fix adds a retry loop specifically for lock conflicts:

```python
_LOCK_RETRY_MAX = 3
_LOCK_RETRY_BASE = 2.0  # seconds

for attempt in range(_LOCK_RETRY_MAX + 1):
    result = shell.run(cmd, timeout=timeout)
    if result.success:
        break
    if "cannot acquire state change lock" in (result.error or ""):
        if attempt < _LOCK_RETRY_MAX:
            time.sleep(_LOCK_RETRY_BASE * (2 ** attempt))
            continue
    break  # non-lock error or retries exhausted
```

This is NOT wired through `_transfer_with_retry()` (Core's backup transfer retry) because that mechanism is for network errors, not virsh lock conflicts. The retry is self-contained in `ExternalSnapshotProvider.create()`.

**Alternatives considered:**
- **A: Move retry to Core's `_create_snapshot()`.** Rejected — Core should not contain domain-specific retry logic. The provider is the right layer for virsh-specific error handling.
- **B: Detect the lock conflict proactively via `virsh domjobinfo`.** Rejected — adds a virsh call to every pipeline run. Reactive retry is simpler and sufficient.

### Decision 6: New config fields on `GlobalConfig` (not per-VM or per-target)

**Rationale:** FULL verification settings apply universally — a corrupt FULL is equally dangerous regardless of which VM or target it belongs to. Placing them on `GlobalConfig` follows the existing pattern (`chain_verify_before_commit`, `chain_verify_after_commit`, `auto_cleanup`, `deep_check_schedule`).

Fields:
- `full_verify_after_create: str = "check"` — `"metadata"`|`"check"`|`"hash"`|`"off"`
- `full_verify_before_rebase: str = "metadata"` — `"metadata"`|`"off"` (minimum "metadata" is enforced)
- `full_verify_before_delete: str = "check"` — `"metadata"`|`"check"`|`"off"` BUT M1 is always enforced regardless of config
- `deep_check_targets: bool = False` — extend `qsnap check --deep` to backup target directories

**Alternatives considered:**
- **A: Per-target verification config.** Rejected — FULL verification is a safety concern, not a performance tuning concern. Uniform enforcement prevents configuration mistakes.
- **B: Per-VM verification config.** Rejected — same reasoning. Also adds complexity to `VMConfig` that serves no practical use case.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| `qemu-img check` (M2) on large FULL files (100GB+) adds minutes to pipeline latency | M2 is configurable via `full_verify_after_create`. Users with large disks can set it to `"metadata"` for fast M1-only checks. `"check"` is the default but explicitly documented as having a time cost. |
| `qemu-img info` (M1) on freshly-created FULL might fail due to filesystem cache not flushed | The file is created via `qemu-img convert` to `.tmp`, then atomically renamed via `mv`. After `mv` returns, the data is guaranteed visible. M1 reads only the qcow2 header (~first few clusters), which are always flushed. Risk is negligible. |
| `virsh domjobabort` fails because the backup job already terminated normally | `domjobabort` returns a non-zero exit when no job is running. We log a WARNING but do not propagate the error. The socket cleanup proceeds. This is safe. |
| Stale state removal (`remove_snapshot()`) before pre-commit chain verification | The pre-commit `_verify_backing_chain()` queries the actual disk chain via `qemu-img info --backing-chain`. It does not use `IStateManager` for chain composition. Removing stale state entries does not affect chain verification. |
| Snapshot lock-conflict retry could mask a genuine persistent lock (e.g., stuck QEMU job) | Retry is capped at 3 attempts with 2s/4s/8s backoff (~14s total). If lock persists, the error propagates. This is sufficient for transient lock conflicts from recently-aborted backup jobs but does not hide persistent issues. |
| `verify_backup_full()` signature diverges from `verify_backup()` | Both live in `verification.py` with clear naming. `verify_backup()` handles incremental transfer verification (source vs target comparison). `verify_backup_full()` handles standalone FULL verification (no source). No existing code paths are changed. |
| Pre-flight cleanup deleting partial `.qcow2` files could delete a valid incremental mid-transfer from another qsnap process | `qsnap` uses a lockfile (`/run/qsnap.lock`) ensuring only one process runs at a time. No concurrent access. |
| Config field `full_verify_before_delete` set to `"off"` could mislead users to think pre-deletion verification is disabled | The spec explicitly states that M1 at pre-deletion is NON-CONFIGURABLE. The config field controls whether M2 (`qemu-img check`) is also run. This must be clearly documented. |

## Migration Plan

1. **State file cleanup**: Existing stale state entries are not automatically cleaned on upgrade. The first pipeline run after deployment will detect stale entries via `os.path.exists()` and remove them via `remove_snapshot()`. No manual intervention required.
2. **Config**: New `GlobalConfig` fields have defaults. Existing TOML config files do not need modification to get the safe defaults. Users can add `[global]` entries to tune verification.
3. **Rollback**: Removing the verification fields from the TOML config restores defaults. The `-F qcow2` fix and `domjobabort` are unconditional — rollback would require reverting the code. No state schema migration is needed (no new `IStateManager` methods).
4. **NBD job abort on upgrade**: Running `virsh domjobabort` for the first time on an orphaned job from a previous version will terminate it cleanly. No special migration step needed.

## Open Questions

None — all design decisions are resolved. The root causes of all eight bugs are well-understood from deep exploration.
