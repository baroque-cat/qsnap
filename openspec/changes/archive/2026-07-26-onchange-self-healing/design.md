## Context

The qsnap backup-side `onchange` gate (`backup_create = "onchange"`) is permanently broken. The gate at `Core._should_backup_onchange()` (`core/__init__.py:2760-2782`) compares `snapshots[-1].allocation` (frozen at 458752 B — the `actual-size` of a fresh qcow2 overlay at snapshot creation time) against `last_backup_allocation` (also 458752, set after the first FULL via `set_last_backup_allocation`). Both values are always identical because `SnapshotInfo.allocation` is stored in state at creation time and never updated. The result: after the initial FULL backup, all subsequent backup transfers are permanently skipped.

Additionally, the system has no self-healing for state-vs-disk inconsistencies. When backup files are manually deleted from the target, the JSON state files (`_full_backups.json`, `_target_state.json`, `_dependencies.json`) retain stale entries. The phantom FULL detection at `core/__init__.py:2816-2833` cleans `_full_backups.json` but does not clean `_target_state.json` or `_dependencies.json`. There is no `clear_last_backup_allocation` method, no `qsnap reconcile` command, and no startup validation. The system is permanently stuck until the user manually edits JSON files under `/var/lib/qsnap/state/`.

The current architecture follows DI with ABC interfaces (AGENTS.md). All state operations go through `IStateManager` ABC. All external commands go through `IShell`. Core is the sole coordinator. CLI is a thin translation layer. These constraints must be preserved.

## Goals / Non-Goals

**Goals:**
- Fix the onchange gate so incremental backups are created when new snapshots exist that are not yet on the target
- Add cascade cleanup so phantom FULL detection also cleans `_dependencies.json` and `_target_state.json`
- Add `clear_last_backup_allocation` and `remove_all_incremental_dependencies` to `IStateManager` ABC
- Add `qsnap reconcile` CLI command that actively repairs state-vs-disk inconsistencies
- Add startup state validation that runs before the onchange gate
- Add auto-cleanup for orphaned libvirt checkpoints
- Separate the onchange gate from retention so expired backups are cleaned even when transfer is skipped
- Add integration tests with real virsh/libvirt/qemu that verify all fixes

**Non-Goals:**
- Changing the snapshot-side onchange gate (`AllocationSizeDetector`) — it works correctly
- Changing the bucket FULL strategy or retention engine — they work correctly when reached
- Adding new state JSON files — all changes use existing `_target_state.json`, `_full_backups.json`, `_dependencies.json`
- Changing the NBD backup transfer mechanism — `transfer_missing()` already handles "no new snapshots" correctly
- Adding new IShell methods — all new commands use existing `IShell.run()`
- Changing the IBackupProvider interface — `provider.list()` already exists and is used by `transfer_missing()`

## Decisions

### Decision 1: Approach B for onchange gate (not Approach A or C)

**Choice:** Replace `_should_backup_onchange()` with a check that calls `provider.list(target)` and compares snapshot names in state against backup names on the target.

**Rationale:** 
- Approach A (use "snapshot was created" signal from snapshot steps) requires passing state between `_execute_snapshot_steps` and `_execute_backup_steps`, which breaks the `_run_pipeline(step_fn: Callable[[VMConfig], bool])` pattern. It also fails for `qsnap backup` (standalone, no snapshot steps) and `snapshot_create = "always"` (signal always True).
- Approach B works for all `snapshot_create` modes, works for `qsnap backup` standalone, requires no signal passing, and uses the existing `provider.list()` method already in `IBackupProvider` ABC.
- Approach C (use `AllocationSizeDetector` in backup gate) fails because `last_allocation` is reset during snapshot creation — by the time backup steps run, the active disk is a fresh overlay (458752 B), so the detector would return False.

**Alternatives considered:**
- Remove the gate entirely and let `transfer_missing()` handle it (it already skips already-backed-up snapshots). Rejected because the bucket FULL check would still run unnecessarily, and the overhead of `provider.list()` + `transfer_missing()` setup is avoidable.

### Decision 2: Cascade cleanup at phantom detection, not separate method

**Choice:** Extend the existing phantom FULL detection loop in `_backup_target()` to also call `remove_all_incremental_dependencies()` and `clear_last_backup_allocation()` when appropriate.

**Rationale:** The phantom detection loop already iterates all FULLs and checks `os.path.exists()`. Adding cascade cleanup in the same loop is the minimal change. A separate `_reconcile_target_state()` method would duplicate the iteration.

### Decision 3: `qsnap reconcile` as a new CLI subcommand, not a `--fix` flag on `check`

**Choice:** Add a new `reconcile` subcommand to the CLI dispatch map, dispatching to `Core.reconcile()`.

**Rationale:** `qsnap check --state` is explicitly read-only (documented in `StateCheckResult` docstring). Adding a `--fix` flag would violate the single-responsibility principle. A separate `reconcile` command is clearer, can have its own `--dry-run` flag, and follows the existing pattern where each CLI command maps to one Core method.

### Decision 4: Startup validation in `_execute_pipeline`, not in `__init__`

**Choice:** Add `_validate_state_at_startup()` as a private method called from `_execute_pipeline()` and `_execute_backup_steps()`, not from `Core.__init__()`.

**Rationale:** `Core.__init__()` just stores DI dependencies (AGENTS.md pattern). State validation needs to run per-VM, not once at construction. It also needs `vm_config` to know which targets to check. Placing it in `_execute_pipeline` ensures it runs before the onchange gate, and placing it in `_execute_backup_steps` ensures standalone `qsnap backup` also gets it.

### Decision 5: `remove_all_incremental_dependencies` as a new IStateManager method

**Choice:** Add a new bulk-removal method instead of looping `remove_incremental_dependency` in Core.

**Rationale:** The existing `remove_incremental_dependency(target_path, incremental_name, full_name)` removes one entry at a time. For cascade cleanup, we need to remove ALL dependencies linked to a FULL. A bulk method is more efficient (single JSON load/save) and keeps the loop logic in the state manager, not in Core. This follows the existing pattern where `clear_deferred_operations(vm_name)` is a bulk operation.

### Decision 6: Gate/retention separation via `skip_transfer` flag

**Choice:** Replace the early `return False` in the onchange gate with a `skip_transfer = True` flag that skips only the transfer section, while retention + cleanup always execute.

**Rationale:** The current early return blocks retention cleanup when the gate skips. This means expired backups accumulate on the target indefinitely if no new snapshots are created. Separating the gate from retention ensures expired backups are always cleaned, regardless of whether new data exists.

## Risks / Trade-offs

- **Double `provider.list(target)` call**: Approach B calls `provider.list()` in the gate, then `transfer_missing()` calls it again (line 219). For 10-20 backup files, this is < 1 second. Optimization (passing result through) would change the `IBackupProvider` interface — deferred to a future change. → *Acceptable overhead for now.*

- **IStateManager ABC breaking change**: Adding 2 abstract methods forces all implementations to update (JsonStateManager, InMemoryStateManager, contract tests). → *Mitigated by providing implementations in the same change. Contract tests are parametrized and will catch any missing implementation.*

- **Startup validation overhead**: `_validate_state_at_startup()` runs `os.path.exists()` on each FULL file for each target. For 3 VMs with 1 target each, this is 3 filesystem stat calls — negligible. → *No mitigation needed.*

- **Reconcile deletes state entries**: Unlike `check --state` (read-only), `reconcile` actively deletes state entries. A bug in reconcile could delete valid state. → *Mitigated by `--dry-run` flag and by only deleting entries where `os.path.exists()` returns False (file genuinely missing).*

- **Orphan checkpoint auto-cleanup**: Auto-deleting checkpoints via `virsh checkpoint-delete --metadata` could delete a checkpoint that's still in use by a running NBD backup job. → *Mitigated by only auto-cleaning in `reconcile()` (explicit user action), not at startup. Startup validation does NOT auto-delete checkpoints.*

- **Gate/retention separation changes behavior**: Previously, when the gate skipped, no retention ran. Now retention always runs. This could delete backups that were previously "frozen" by the gate skip. → *This is the desired behavior — expired backups should be deleted regardless of new data availability.*
