## Why

qsnap currently has two architectural gaps:

1. **No configurable snapshot preservation floor.** When `snapshot_chain_length` is exceeded, blockcommit merges ALL snapshots in the retention `remove` list. The only safety mechanism is the oldest-prefix post-processing (which prevents gapped commits), but there is no user-configurable way to guarantee that the newest N snapshots are always preserved regardless of `chain_length`. The `preserve_snapshots` CLI flag is all-or-nothing. Operators need a middle ground: "collapse old snapshots, but always keep the last N for rollback."

2. **Target onchange gate is coupled to snapshot creation.** The `backup_create="onchange"` gate (`Core._should_backup_onchange`) compares snapshot NAMES from state against backup files on the target. This fails when snapshots are not created (e.g., `snapshot_create="ondemand"` with no reachable target) — the disk may have changed, but the gate returns False and no backup is made. It also creates unnecessary backups when `snapshot_create="always"` creates snapshots even though the disk hasn't changed. The deprecated `last_backup_allocation` state infrastructure exists but is explicitly prohibited by the current spec.

## What Changes

### Part 1: `snapshot_preserve_min` — Non-Collapse Zone

- Add `snapshot_preserve_min: int = 0` to `GlobalConfig` (default 0 = inactive)
- Add `snapshot_preserve_min: int | None = None` to `VMConfig` (inherits from global via option inheritance)
- Add `preserve_min: int = 0` to `RetentionPolicy` dataclass (third field, default 0 = inactive)
- Add a new post-processing step in `Core._evaluate_snapshot_retention()`, applied AFTER the existing oldest-prefix filter: if `len(remove) > len(snapshots) - preserve_min`, trim `remove` to the oldest `len(snapshots) - preserve_min` items and move the trimmed items to `keep`
- **BREAKING**: The `RetentionPolicy` dataclass gains a third field (`preserve_min`). All `RetentionPolicy(...)` construction sites must be updated. The spec previously stated "exactly two fields" — this is now three fields.
- **BREAKING**: `GlobalConfig` and `VMConfig` previously explicitly prohibited `snapshot_preserve_min`. This prohibition is lifted.

### Part 2: Independent Target Onchange via Source-Disk Change Detection

- Replace `Core._should_backup_onchange()` with a source-disk-based change detection mechanism that queries the VM's active disk directly, independent of snapshot existence
- Use the existing `IChangeDetector` infrastructure (selected by `VMConfig.change_detection_mode`) to compare the source disk's current state against a per-target baseline stored in `IStateManager.get_last_backup_allocation(target_path)`
- After a successful backup, call `set_last_backup_allocation(target_path, current_allocation)` to update the per-target baseline
- **BREAKING**: The `change-detection` spec previously stated "SHALL NOT read `last_backup_allocation`" and "SHALL NOT call `set_last_backup_allocation`". These prohibitions are lifted — `last_backup_allocation` is now the per-target change-detection baseline.
- The `IStateManager.get/set_last_backup_allocation` methods (already implemented in `JsonStateManager` and `InMemoryStateManager`) are un-deprecated
- The snapshot-name comparison (Approach B) is removed entirely — the gate no longer depends on snapshots existing
- When `backup_create="onchange"` and no prior baseline exists (first run), the gate returns True (backup proceeds)

## Capabilities

### New Capabilities

- `snapshot-preserve-min`: Configurable minimum snapshot preservation floor — ensures the newest N snapshots are never blockcommitted, even when `snapshot_chain_length` is exceeded. Applied as a post-processing filter in `Core._evaluate_snapshot_retention()` after the oldest-prefix filter.
- `independent-target-onchange`: Source-disk-based change detection for the `backup_create="onchange"` gate. Replaces the snapshot-name comparison (Approach B) with direct `IChangeDetector.has_changed()` queries against a per-target baseline stored in `IStateManager`. Decouples backup decisions from snapshot creation.

### Modified Capabilities

- `count-based-retention`: `RetentionPolicy` gains a third field `preserve_min: int = 0`. The retention engine itself (pure function) is unchanged — `preserve_min` is applied as a post-processing filter in Core, not inside `evaluate()`. The spec's "exactly two fields" requirement is updated to three.
- `config-model`: `GlobalConfig` gains `snapshot_preserve_min: int = 0`. `VMConfig` gains `snapshot_preserve_min: int | None = None`. The explicit prohibitions on `snapshot_preserve_min` are lifted. `GlobalConfig` gains `backup_create: str = "always"` (already exists but needs spec alignment with the new onchange semantics).
- `change-detection`: The backup-side onchange gate is fundamentally reworked. The "SHALL NOT read `last_backup_allocation`" and "SHALL NOT call `set_last_backup_allocation`" requirements are replaced with requirements that mandate their use. The snapshot-name comparison (Approach B) is removed.
- `state-management`: The `get_last_backup_allocation`/`set_last_backup_allocation`/`clear_last_backup_allocation` methods are un-deprecated and documented as the per-target change-detection baseline. No interface changes — the methods already exist and are tested.

## Impact

### Code Changes

| File | Change |
|------|--------|
| `qsnap/models/config.py` | Add `preserve_min` to `RetentionPolicy`, `snapshot_preserve_min` to `GlobalConfig` and `VMConfig` |
| `qsnap/core/__init__.py` | Add preserve_min post-processing in `_evaluate_snapshot_retention()`; replace `_should_backup_onchange()` with detector-based logic; add `set_last_backup_allocation()` call after successful backup |
| `qsnap/config/facade.py` | Parse and resolve `snapshot_preserve_min` via option inheritance |
| `qsnap/interfaces/state.py` | No changes — methods already exist |
| `qsnap/state/json_manager.py` | No changes — methods already implemented |
| `qsnap/tests/mocks/mock_state.py` | No changes — `InMemoryStateManager` already implements these methods |

### Spec Changes

| Spec | Change |
|------|--------|
| `count-based-retention` | Delta: add `preserve_min` field, update "two fields" to "three fields" |
| `config-model` | Delta: add `snapshot_preserve_min` to GlobalConfig/VMConfig, lift prohibitions |
| `change-detection` | Delta: replace Approach B with source-disk detection, un-deprecate `last_backup_allocation` |
| `state-management` | Delta: un-deprecate `last_backup_allocation`, document as per-target baseline |
| `snapshot-preserve-min` | New spec |
| `independent-target-onchange` | New spec |

### Test Impact

- `tests/core/test_retention_policy.py` — update for `preserve_min` field
- `tests/core/test_preserve.py` — update for new `snapshot_preserve_min` parameter
- `tests/core/test_onchange.py` — **rewrite**: remove Approach B tests, add detector-based tests
- `tests/modules/change/test_allocation.py` — no changes (detector logic unchanged)
- `tests/modules/change/test_map_detector.py` — no changes (detector logic unchanged)
- `tests/integration/test_onchange.py` — **rewrite**: verify new source-disk-based behavior with real libvirt/qemu
- New integration tests for `snapshot_preserve_min` with real blockcommit
- New unit tests for the preserve_min post-processing filter
