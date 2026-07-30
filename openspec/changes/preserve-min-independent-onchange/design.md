## Context

qsnap manages external qcow2 snapshots and bitmap-based backups for QEMU/KVM VMs. Two architectural gaps have been identified through deep codebase analysis:

1. **Snapshot preservation**: The retention engine (`TimeBasedRetention`) keeps the newest N snapshots and marks the oldest for blockcommit. An oldest-prefix post-processing step ensures only contiguous oldest snapshots are committed. However, there is no configurable floor — operators cannot say "always keep the last 24 snapshots for rollback, even if chain_length is exceeded." The `preserve_snapshots` CLI flag is all-or-nothing.

2. **Target onchange coupling**: The `backup_create="onchange"` gate (`Core._should_backup_onchange`) compares snapshot names from `IStateManager.get_snapshots()` against backup file names on the target via `provider.list(target)`. This creates a hard dependency on snapshot existence. When snapshots are not created (`snapshot_create="ondemand"` with no reachable target, or `snapshot_create="onchange"` and disk unchanged), the gate returns False — no backup is made even if the disk has changed. Conversely, when `snapshot_create="always"` creates snapshots without disk changes, the gate opens and creates unnecessary backups.

The deprecated `last_backup_allocation` state infrastructure (methods exist in `IStateManager`, implemented in `JsonStateManager` and `InMemoryStateManager`, tested in contract tests) provides a ready-made per-target baseline storage. The existing `IChangeDetector` implementations (`AllocationSizeDetector`, `MapChangeDetector`) already query the source disk directly via `qemu-img info` and `qemu-img map`.

## Goals / Non-Goals

**Goals:**
- Add `snapshot_preserve_min` as a configurable parameter (global → VM inheritance) that guarantees the newest N snapshots are never blockcommitted
- Replace the snapshot-name-based onchange gate with source-disk-based change detection using the existing `IChangeDetector` infrastructure
- Un-deprecate `last_backup_allocation` as the per-target change-detection baseline
- Maintain all existing verification mechanisms (pre-commit chain verification, post-commit verification, deep verify, verify-before-delete, etc.)
- Keep the retention engine (`TimeBasedRetention`) as a pure function — `preserve_min` is applied in Core post-processing, not inside `evaluate()`

**Non-Goals:**
- Changing the retention engine's algorithm (it remains: sort, keep newest N, remove oldest)
- Changing the oldest-prefix post-processing (it remains as-is, before preserve_min)
- Modifying `IChangeDetector` interface or its implementations
- Changing `IStateManager` interface (methods already exist)
- Adding new factory branches (existing `create_change_detector` is reused)
- Changing the snapshot-side onchange (`snapshot_create="onchange"` — unchanged)
- Using libvirt checkpoints/dirty bitmaps for change detection (they remain solely for NBD transfer)

## Decisions

### D1: preserve_min as Core post-processing, not inside the retention engine

**Decision:** `preserve_min` is applied in `Core._evaluate_snapshot_retention()` as a post-processing step AFTER the oldest-prefix filter, NOT inside `TimeBasedRetention.evaluate()`.

**Rationale:** The retention engine is a pure function with no I/O (design principle). Adding `preserve_min` to `evaluate()` would require passing it through `RetentionPolicy`, which is already used for both snapshot and target retention. Keeping it in Core post-processing:
- Preserves the pure-function nature of the engine
- Follows the existing pattern (oldest-prefix is already a Core post-processing step)
- Allows `preserve_min` to be snapshot-specific (target retention doesn't need it)
- The `RetentionPolicy` dataclass gains the field for transport, but the engine ignores it

**Alternative considered:** Add `preserve_min` logic inside `evaluate()`. Rejected because it would couple the engine to snapshot-specific semantics (target retention doesn't use preserve_min), and the engine's contract is "keep newest N, remove oldest" — preserve_min is a safety filter, not a retention policy.

### D2: preserve_min trims from the NEWEST end of the remove list

**Decision:** When `len(remove) > len(snapshots) - preserve_min`, the excess items are moved from `remove` to `keep`. The excess items are the NEWEST items in the remove list (those closest to the keep boundary).

**Rationale:** The remove list is ordered oldest-first (by timestamp). Trimming from the newest end of remove means we keep committing the oldest snapshots (which are the most expendable) and preserve the newer ones (which are closer to the active layer and more useful for rollback).

**Example:** 100 snapshots, `chain_length=72`, `preserve_min=24`:
- Retention: keep=72 (newest), remove=28 (oldest)
- Oldest-prefix: remove=28 (contiguous, unchanged)
- preserve_min: max_removable = 100 - 24 = 76. Since 28 ≤ 76, no trimming needed. All 28 are committed.
- Result: 72 snapshots remain (which is ≥ 24, satisfying preserve_min)

**Example:** 30 snapshots, `chain_length=6`, `preserve_min=24`:
- Retention: keep=6 (newest), remove=24 (oldest)
- Oldest-prefix: remove=24 (contiguous, unchanged)
- preserve_min: max_removable = 30 - 24 = 6. Since 24 > 6, trim to 6. Move 18 newest remove items to keep.
- Result: remove=6 (oldest), keep=24 (newest). 6 are committed, 24 preserved.

### D3: Replace Approach B entirely with source-disk detection

**Decision:** `Core._should_backup_onchange()` is replaced with a new implementation that:
1. Creates a change detector via `factory.create_change_detector(vm_config.change_detection_mode)`
2. Calls `detector.has_changed(vm_config)` — this queries the source disk directly
3. The detector compares against `IStateManager.get_last_allocation(vm_name)` (the snapshot-side baseline)

**Wait — this is wrong.** The snapshot-side baseline (`get_last_allocation`) is updated after snapshot creation, not after backup. If snapshots are created between backups, the baseline would reflect the last snapshot, not the last backup. We need a per-target baseline.

**Corrected decision:** The gate uses `IStateManager.get_last_backup_allocation(target_path)` as the per-target baseline. But the existing `IChangeDetector.has_changed()` reads from `get_last_allocation(vm_name)`, not `get_last_backup_allocation(target_path)`.

**Two options:**
- **Option A:** Add a new method to `IChangeDetector` that accepts a custom baseline
- **Option B:** Use the existing detector but manually compare the `ChangeResult.current_allocation` against `get_last_backup_allocation(target_path)`

**Decision: Option B.** The existing `has_changed()` returns `ChangeResult(changed, last_allocation, current_allocation)`. Core can:
1. Call `detector.has_changed(vm_config)` to get `current_allocation` (the detector resolves the active disk and queries it)
2. Ignore `result.changed` (which compares against the snapshot-side baseline)
3. Compare `result.current_allocation` against `state.get_last_backup_allocation(target_path)`
4. If `current_allocation > last_backup_allocation` (for allocation-size) or `!=` (for allocation-map) → changed

**Rationale:** This avoids changing the `IChangeDetector` interface. The detector is a stateless worker — it queries the disk and returns the current value. Core owns the comparison logic and the baseline source.

**Alternative considered:** Add `has_changed(vm_config, baseline_override: int | None = None)` to `IChangeDetector`. Rejected because it changes the ABC interface (BREAKING for all implementations and mocks), and the detector's contract is "compare against the stored baseline" — adding a custom baseline is a Core concern, not a detector concern.

### D4: Per-target baseline updated after successful backup

**Decision:** After a successful backup transfer (FULL or incremental), Core calls `state.set_last_backup_allocation(target_path, result.current_allocation)` where `result` is the `ChangeResult` obtained at the start of the backup step.

**Rationale:** The baseline must reflect the source disk state at the time of the last successful backup. If the backup fails, the baseline is NOT updated — the next run will detect "changed" and retry. This is fail-safe.

**Implementation detail:** Core obtains the `ChangeResult` once at the start of `_backup_target()` (when `backup_create="onchange"`), then uses it for both the gate decision and the baseline update. If the gate returns False (unchanged), the baseline is not updated (it's already current). If the gate returns True (changed), the backup proceeds, and on success the baseline is updated.

### D5: First-run behavior — no baseline means "changed"

**Decision:** When `get_last_backup_allocation(target_path)` returns `None` (first run or after `clear_last_backup_allocation`), the gate returns True (backup proceeds).

**Rationale:** This matches the existing `IChangeDetector` behavior (first run returns `changed=True`). It ensures the first backup to a target always happens.

### D6: RetentionPolicy gains preserve_min field for transport only

**Decision:** `RetentionPolicy` gains `preserve_min: int = 0` as a third field. The `TimeBasedRetention.evaluate()` method IGNORES this field — it is only consumed by Core's post-processing.

**Rationale:** `RetentionPolicy` is constructed by Core and passed to the factory/engine. Adding the field here allows Core to pass it through the existing data flow without introducing a new parameter. The engine's pure-function contract is preserved (it ignores the field). The spec's "exactly two fields" requirement is updated to "three fields."

## Risks / Trade-offs

- **[Risk] `qemu-img map` performance on large disks** → The MapChangeDetector runs `qemu-img map` on the source's active disk. For large, fragmented disks, this can be slow. Mitigation: this is the same cost as the snapshot-side onchange gate (which already runs the detector). The backup gate runs once per target per pipeline run, not per snapshot.

- **[Risk] Switching `change_detection_mode` mid-lifecycle** → Both `AllocationSizeDetector` and `MapChangeDetector` store their value under the same `last_allocation` key (snapshot-side) and `last_backup_allocation` key (target-side). Switching modes would cause a type mismatch (byte count vs hash). Mitigation: fail-safe — the first run after switching will detect "changed" (mismatch) and proceed with a backup. The baseline is then updated with the correct type.

- **[Risk] `preserve_min` > total snapshots** → If `preserve_min` is set higher than the total snapshot count, no snapshots are ever committed. Mitigation: this is by design — the operator explicitly requested this floor. The oldest-prefix filter still applies. If `preserve_min=0` (default), the feature is inactive.

- **[Trade-off] `RetentionPolicy` is no longer "exactly two fields"** → The spec explicitly prohibited a third field. This is a deliberate breaking change. The field defaults to 0 (inactive), so existing behavior is preserved when not configured.

- **[Trade-off] `last_backup_allocation` semantics change** → Previously stored source `actual-size` for comparison (deprecated Approach A). Now stores either `actual-size` or allocation-map hash (depending on `change_detection_mode`). The storage format (integer in `_target_state.json`) is unchanged — only the semantics of the stored value change based on the detector mode.

- **[Risk] Detector queries source disk even when VM is shut off** → `AllocationSizeDetector` and `MapChangeDetector` resolve the active disk via `virsh domblklist`. When the VM is shut off, `domblklist` returns the base image path. The detector still works (queries the base image's allocation). This is correct — if the VM is shut off, no new data is being written, and the allocation should match the baseline (unchanged).
