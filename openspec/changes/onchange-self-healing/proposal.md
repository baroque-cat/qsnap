## Why

The backup-side `onchange` gate (`backup_create = "onchange"`) is fundamentally broken: it compares `snapshots[-1].allocation` (frozen at 458752 B — the size of a fresh qcow2 overlay at snapshot creation time) against `last_backup_allocation` (also 458752, set after the first FULL). Both values are always identical, so the gate permanently skips all backup transfers after the initial FULL. Additionally, the system lacks any self-healing mechanism for state-vs-disk inconsistencies: manually deleted backup files leave stale JSON state entries that block future backups indefinitely, with no programmatic way to reset them.

## What Changes

- **Replace onchange gate logic (Approach B)**: Instead of comparing frozen allocation values, the gate checks whether any snapshot in state is not yet backed up to the target (via `provider.list(target)`). This is independent of `snapshot_create` mode and works for `always`/`onchange`/`ondemand`.
- **Add `clear_last_backup_allocation` to IStateManager**: New ABC method to reset the per-target backup allocation baseline. Currently no programmatic way exists to clear this value — only manual JSON file editing.
- **Add `remove_all_incremental_dependencies` to IStateManager**: New ABC method for cascade cleanup of all dependencies linked to a FULL when it is removed as a phantom.
- **Cascade cleanup at phantom FULL detection**: When a phantom FULL is detected (file missing on disk), the system now also removes linked incremental dependencies from `_dependencies.json` and clears `last_backup_allocation` if no FULLs remain. Currently only `_full_backups.json` is cleaned.
- **Add `qsnap reconcile` CLI command**: A new subcommand that actively repairs state-vs-disk inconsistencies (phantom snapshots, phantom FULLs, stale dependencies, stale baselines, orphan checkpoints). Unlike `qsnap check --state` (read-only), `reconcile` makes fixes.
- **Add startup state validation**: A lightweight state-vs-disk check at the beginning of the pipeline (before the onchange gate) that detects and cleans phantom FULLs and stale baselines, so the gate sees correct state.
- **Auto-cleanup orphan checkpoints**: `_detect_orphan_checkpoints()` gains an `auto_cleanup` parameter that deletes orphaned libvirt checkpoints via `virsh checkpoint-delete --metadata` instead of only reporting them.
- **Separate onchange gate from retention**: When the gate skips transfer, retention + cleanup still run (to delete expired backups). Currently the gate's early return blocks all downstream logic including retention.
- **Remove `set_last_backup_allocation` call**: The post-backup `set_last_backup_allocation()` call becomes dead code since the gate no longer uses `last_backup_allocation`. The method stays in the interface for compatibility but is no longer called from the pipeline.

## Capabilities

### New Capabilities

- `state-reconciliation`: Active state-vs-disk repair via `qsnap reconcile` CLI command. Removes phantom snapshots, phantom FULLs (with cascade dependency cleanup), stale baselines, and orphan checkpoints. Returns `ReconcileResult` with counts of items fixed.
- `startup-state-validation`: Lightweight state-vs-disk validation at pipeline start (before onchange gate). Detects phantom FULLs, cleans stale `last_backup_allocation` baselines, and ensures the gate sees correct state. Non-fatal: logs warnings, never raises.

### Modified Capabilities

- `change-detection`: The backup-side onchange gate (`_should_backup_onchange`) is replaced with Approach B — checking whether snapshots in state exist on the target via `provider.list(target)`, instead of comparing frozen allocation values.
- `state-management`: **BREAKING** — IStateManager ABC gains two new abstract methods: `clear_last_backup_allocation(target_path) -> bool` and `remove_all_incremental_dependencies(target_path, full_name) -> int`. All implementations (JsonStateManager, InMemoryStateManager) must be updated.
- `core-orchestrator`: Phantom FULL detection in `_backup_target` gains cascade cleanup (dependencies + baseline). The onchange gate and retention are separated — retention always runs even when transfer is skipped. New `_validate_state_at_startup` method called before snapshot/backup steps.
- `cli-interface`: New `reconcile` subcommand added to the dispatch map. Accepts `--dry-run` and `--format` flags.
- `state-consistency-check`: `check_state()` logic is reused by `reconcile()` but `reconcile` actively fixes issues instead of only reporting them. `_detect_orphan_checkpoints()` gains `auto_cleanup` parameter.

## Impact

- **IStateManager ABC** (`interfaces/state.py`): +2 abstract methods — all implementations must update (JsonStateManager, InMemoryStateManager, any contract tests)
- **JsonStateManager** (`state/json_manager.py`): +2 new methods, no changes to existing methods
- **InMemoryStateManager** (`tests/mocks/mock_state.py`): +2 new methods mirroring JsonStateManager
- **Core** (`core/__init__.py`): Modified `_should_backup_onchange` (replaced), modified `_backup_target` (cascade cleanup + gate/retention separation), new `_validate_state_at_startup`, new `reconcile` public method, modified `_detect_orphan_checkpoints` (auto_cleanup param)
- **Models** (`models/results.py`): New `ReconcileResult` frozen dataclass
- **CLI** (`cli/app.py`, `cli/commands.py`): New `reconcile` subcommand in dispatch map + handler
- **Unit tests** (`tests/core/`, `tests/state/`, `tests/cli/`): Existing onchange gate tests must be rewritten (old logic replaced); new tests for new state methods and reconcile command
- **Contract tests** (`tests/interfaces/test_state_manager.py`): Must parametrize new methods
- **Integration tests** (`tests/integration/`): New tests for onchange gate (Approach B), manual deletion recovery, reconcile command, startup validation, retention-on-skip
- **State files**: No schema migration needed — existing `_target_state.json` entries will be cleaned by reconcile/startup validation; no new JSON files created
- **No new external dependencies**: All changes use existing stdlib + IShell + IStateManager infrastructure
