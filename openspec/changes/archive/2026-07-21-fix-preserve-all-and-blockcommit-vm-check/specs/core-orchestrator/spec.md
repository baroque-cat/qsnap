## MODIFIED Requirements

### Requirement: Pipeline step order

`Core._execute_pipeline(vm_config)` SHALL execute steps in this order:
1. Pre-flight environment validation (including stale file cleanup per `auto_cleanup`)
2. Deferred blockcommit check — state-adaptive drain per the `deferred-operations` capability
3. Change detection — if `snapshot_create` mode requires it
4. Snapshot creation — if detector says we should, or if mode is "always"
5. Snapshot retention evaluation — which snapshots to keep/remove
6. Snapshots to merge: pre-commit backing chain integrity verification (per `chain_verify_before_commit`)
7. Snapshot lifecycle — **adaptive blockcommit**: Core SHALL determine the VM power state via `virsh domstate` and the active overlay path via `virsh domblklist`, split the remove set into committable and deferrable subsets, execute the committable subset with the mechanism valid for the current state, and defer the rest. MAC denial deferral applies as before.
8. Post-commit chain length verification (per `chain_verify_after_commit`)
9. Per-target backup transfer → backup verification → backup retention → cleanup

The `--preserve-snapshots` and `--dry-run` guards SHALL run before any `virsh` state-detection calls.

The adaptive fork in step 7 SHALL behave as follows:

| VM state (`domstate`) | `lifecycle_mode` | Committable subset | Executor | Deferred subset and reason |
|---|---|---|---|---|
| running | `virsh` | remove set minus the active layer | `BlockCommitManager` | active layer, reason `"vm_running"` |
| running | `qemu-img` | (none) | — | entire remove set, reason `"vm_running"` |
| shut off | any | remove set minus the XML-referenced tip overlay | `QemuImgCommitManager` | tip overlay, reason `"active_layer"` |
| paused / other | any | (none) | — | entire remove set, reason `"vm_running"` |
| domstate call failed | any | entire remove set (legacy fallback) | manager for configured mode | (none) |

The active-layer path SHALL be obtained from `virsh domblklist` (via `parse_domblklist_path()`); on failure Core SHALL fall back to the newest snapshot recorded in `IStateManager` and log a WARNING. When the executor is `QemuImgCommitManager`, Core SHALL re-check `virsh domstate` immediately before invoking the manager; if the VM is no longer shut off, Core SHALL defer the committable subset with reason `"vm_running"` instead.

After any successful commit (any branch), Core SHALL remove the committed snapshots from `IStateManager` unconditionally — independent of `chain_verify_after_commit` — and append one `ActionRecord("snapshot_delete")` per committed snapshot.

After any successful OFFLINE commit (executor `QemuImgCommitManager`, main path or deferred drain), Core SHALL refresh the domain's persistent XML so it no longer references deleted overlay files: dump the XML via `virsh dumpxml`, remove every `<backingStore>` element from every `<disk>` element, and redefine the domain via `virsh define`. With no `<backingStore>` recorded, libvirt re-probes the shortened chain from qcow2 headers on next start. Refresh failures SHALL be non-fatal WARNINGs (the commit itself already succeeded).

#### Scenario: Non-active snapshots committed live when VM is running (virsh mode)
- **WHEN** `lifecycle_mode = "virsh"`, `virsh domstate` returns "running", and the remove set contains only non-active snapshots
- **THEN** `factory.create_lifecycle_manager(mode="virsh")` is used
- **AND** `manager.blockcommit()` is called with the full remove set
- **AND** no deferred entry is created

#### Scenario: Active layer deferred when VM is running (virsh mode)
- **WHEN** `lifecycle_mode = "virsh"`, `virsh domstate` returns "running", and the remove set contains the active overlay (per `domblklist`)
- **THEN** the non-active prefix is committed live via `BlockCommitManager`
- **AND** the active snapshot is deferred via `add_deferred_blockcommit()` with reason `"vm_running"`
- **AND** an INFO log records the split decision

#### Scenario: qemu-img mode defers everything when VM is running
- **WHEN** `lifecycle_mode = "qemu-img"` and `virsh domstate` returns "running"
- **THEN** no manager is invoked
- **AND** the entire remove set is deferred with reason `"vm_running"`
- **AND** the pipeline continues to backup steps

#### Scenario: Blockcommit deferred when VM is paused
- **WHEN** `virsh domstate` returns "paused"
- **THEN** no manager is invoked regardless of `lifecycle_mode`
- **AND** the entire remove set is deferred with reason `"vm_running"`

#### Scenario: Offline commit via qemu-img when VM is shut off
- **WHEN** `virsh domstate` returns "shut off" (either lifecycle mode) and the remove set does not contain the XML-referenced tip overlay
- **THEN** `factory.create_lifecycle_manager(mode="qemu-img")` is used
- **AND** `manager.blockcommit()` is called with the full remove set
- **AND** no deferred entry is created

#### Scenario: XML-referenced tip excluded from offline commit
- **WHEN** `virsh domstate` returns "shut off" and the remove set contains the overlay referenced by the inactive domain XML (per `domblklist`)
- **THEN** the remaining snapshots are committed via `QemuImgCommitManager`
- **AND** the tip overlay is deferred with reason `"active_layer"`
- **AND** the tip file is never passed to the manager, so the domain remains bootable

#### Scenario: VM state check failure is non-fatal
- **WHEN** `virsh domstate` fails (e.g., VM not defined, libvirt not running)
- **THEN** blockcommit proceeds with the manager for the configured `lifecycle_mode` and the full remove set (legacy behavior)
- **AND** no deferral occurs

#### Scenario: Race guard before offline commit
- **WHEN** the plan selected the `QemuImgCommitManager` executor but the immediate `virsh domstate` re-check no longer returns "shut off"
- **THEN** the manager is not invoked
- **AND** the committable subset is deferred with reason `"vm_running"`

#### Scenario: State entries removed unconditionally after commit
- **WHEN** a blockcommit succeeds and `chain_verify_after_commit` is disabled
- **THEN** the committed snapshots are still removed from `IStateManager`
- **AND** subsequent backup steps operate on the survivor list only

#### Scenario: Domain XML refreshed after offline commit
- **WHEN** an offline commit via `QemuImgCommitManager` succeeds and committed overlay files are deleted
- **THEN** the domain's persistent XML no longer contains `<backingStore>` elements referencing the deleted files
- **AND** `virsh start` on the domain succeeds (libvirt re-probes the shortened chain)

#### Scenario: preserve="all" with VM running — no blockcommit attempted
- **WHEN** `snapshot_preserve = "all"` and the VM is running
- **THEN** the retention engine keeps all snapshots (after D1 fix)
- **AND** `_blockcommit_snapshots()` is not called (empty remove list)
- **AND** no blockcommit error occurs
