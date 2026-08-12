# Deferred Operations — delta

## MODIFIED Requirements

### Requirement: Per-disk deferred blockcommit queue in IStateManager

`IStateManager` SHALL provide methods for managing a deferred blockcommit queue: `get_deferred_operations(vm_name) -> list[DeferredBlockcommit]`, `add_deferred_blockcommit(vm_name, disk, snapshots, reason)`, `clear_deferred_operations(vm_name)`, `update_deferred_warning(vm_name, index, timestamp)`. `DeferredBlockcommit` SHALL be a dataclass with fields `snapshots: list[str]`, `reason: str`, `since: datetime`, `disk: str`, and `last_warned_at: datetime | None` (default `None`). Each entry belongs to a specific disk target (e.g. `"vda"`) identified by the `disk` field.

The `reason` field SHALL accept any string value. Known values include `"apparmor"`, `"selinux"`, `"vm_running"`, `"active_layer"`, `"enospc"`, `"blockjob_active"`, and `"vm_state_unknown"`. The `"vm_running"` reason is used when Core defers blockcommit because the VM is not in "shut off" state (running or paused). The `"active_layer"` reason is used when Core defers the XML-referenced tip overlay of an inactive domain, which must never be committed or deleted offline (the domain would become unbootable). The `"enospc"` reason is used when a blockcommit fails with a space-classified error (target or snapshot filesystem full); the merge is retried once free space is restored. The `"blockjob_active"` reason is used when Core detects an active libvirt block job on the disk (own zombie job pending reconciliation or unknown foreign job) and refuses to start a competing commit; the merge is retried once the job is gone and reconciliation allows it. The `"vm_state_unknown"` reason is used when Core cannot determine the VM or job state (failed `domstate` re-check before offline commit, failed block-job probe, or inconclusive reconciliation); the deferral is fail-closed and the merge is retried when state becomes observable.

#### Scenario: Add and retrieve per-disk deferred blockcommit

- **WHEN** `add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "apparmor")` is called
- **THEN** `get_deferred_operations("vm1")` returns a list containing one `DeferredBlockcommit` with snapshots=["snap1.qcow2"], reason="apparmor", disk="vda", last_warned_at=None

#### Scenario: Multiple disks can have separate deferred entries

- **WHEN** `add_deferred_blockcommit("vm1", "vda", ["vda_snap1.qcow2"], "apparmor")` is called
- **AND** `add_deferred_blockcommit("vm1", "vdb", ["vdb_snap1.qcow2"], "selinux")` is called
- **THEN** `get_deferred_operations("vm1")` returns two entries with disk="vda" and disk="vdb" respectively

#### Scenario: Add deferred blockcommit with vm_running reason

- **WHEN** `add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2", "snap2.qcow2"], "vm_running")` is called
- **THEN** `get_deferred_operations("vm1")` returns a list containing one `DeferredBlockcommit` with reason="vm_running" and disk="vda"

#### Scenario: Add deferred blockcommit with active_layer reason

- **WHEN** `add_deferred_blockcommit("vm1", "vda", ["snap3.qcow2"], "active_layer")` is called
- **THEN** `get_deferred_operations("vm1")` returns a list containing one `DeferredBlockcommit` with reason="active_layer" and disk="vda"

#### Scenario: Add deferred blockcommit with enospc reason
- **WHEN** `add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "enospc")` is called
- **THEN** `get_deferred_operations("vm1")` returns a list containing one `DeferredBlockcommit` with reason="enospc" and disk="vda"

#### Scenario: Add deferred blockcommit with blockjob_active reason

- **WHEN** `add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "blockjob_active")` is called
- **THEN** `get_deferred_operations("vm1")` returns a list containing one `DeferredBlockcommit` with reason="blockjob_active" and disk="vda"

#### Scenario: Add deferred blockcommit with vm_state_unknown reason

- **WHEN** `add_deferred_blockcommit("vm1", "vda", ["snap1.qcow2"], "vm_state_unknown")` is called
- **THEN** `get_deferred_operations("vm1")` returns a list containing one `DeferredBlockcommit` with reason="vm_state_unknown" and disk="vda"

#### Scenario: Clear deferred operations

- **WHEN** `clear_deferred_operations("vm1")` is called after adding two deferred items
- **THEN** `get_deferred_operations("vm1")` returns an empty list

#### Scenario: No deferred operations for VM

- **WHEN** `get_deferred_operations("vm_new")` is called for a VM with no deferred state
- **THEN** the method returns an empty list

#### Scenario: last_warned_at persists across state round-trip

- **WHEN** a deferred blockcommit with `last_warned_at=datetime(2025, 7, 13)` is written to state and read back
- **THEN** `last_warned_at` has the same value

#### Scenario: Old state file without last_warned_at is backward-compatible

- **WHEN** a state JSON file lacks `last_warned_at` in a deferred entry
- **THEN** `_dict_to_deferred()` constructs a `DeferredBlockcommit` with `last_warned_at=None`
