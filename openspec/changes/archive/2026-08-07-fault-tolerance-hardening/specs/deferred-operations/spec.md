# Deferred Operations — Delta

## MODIFIED Requirements

### Requirement: Per-disk deferred blockcommit queue in IStateManager

`IStateManager` SHALL provide methods for managing a deferred blockcommit queue: `get_deferred_operations(vm_name) -> list[DeferredBlockcommit]`, `add_deferred_blockcommit(vm_name, disk, snapshots, reason)`, `clear_deferred_operations(vm_name)`, `update_deferred_warning(vm_name, index, timestamp)`. `DeferredBlockcommit` SHALL be a dataclass with fields `snapshots: list[str]`, `reason: str`, `since: datetime`, `disk: str`, and `last_warned_at: datetime | None` (default `None`). Each entry belongs to a specific disk target (e.g. `"vda"`) identified by the `disk` field.

The `reason` field SHALL accept any string value. Known values include `"apparmor"`, `"selinux"`, `"vm_running"`, `"active_layer"`, and `"enospc"`. The `"vm_running"` reason is used when Core defers blockcommit because the VM is not in "shut off" state (running or paused). The `"active_layer"` reason is used when Core defers the XML-referenced tip overlay of an inactive domain, which must never be committed or deleted offline (the domain would become unbootable). The `"enospc"` reason is used when a blockcommit fails with a space-classified error (target or snapshot filesystem full); the merge is retried once free space is restored.

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

## ADDED Requirements

### Requirement: Blockcommit space errors deferred instead of aborting

When a blockcommit (live `virsh blockcommit` or offline `qemu-img commit`/rebase) fails
and the error is space-classified (`is_space_error` returns `True`), Core SHALL record
the failed snapshots as a per-disk deferred operation with `reason="enospc"` via
`add_deferred_blockcommit` and SHALL NOT raise `RuntimeError`. The snapshot state
records SHALL remain intact. The entry SHALL be drained by the standard state-adaptive
drain path on subsequent runs and SHALL count toward the standard deferred threshold
monitoring (warn/crit count and age).

#### Scenario: Offline commit ENOSPC defers and continues

- **WHEN** `QemuImgCommitManager.blockcommit()` fails with "No space left on device"
- **THEN** Core adds a deferred entry with `disk=<affected_disk>` and `reason="enospc"`
- **AND** no `RuntimeError` is raised
- **AND** the VM pipeline continues with remaining disks/steps

#### Scenario: Live commit ENOSPC defers and continues

- **WHEN** `BlockCommitManager.blockcommit()` fails with a space-classified error
- **THEN** Core adds a deferred entry with `reason="enospc"`
- **AND** the snapshots remain in state for the next drain

#### Scenario: Deferred enospc entry appears in monitoring

- **WHEN** a deferred entry with reason `"enospc"` exists at the end of a run
- **THEN** `_check_deferred_thresholds()` applies the standard count/age thresholds to it
- **AND** `qsnap list deferred` shows the entry with reason `enospc`

#### Scenario: Non-space commit failure still aborts

- **WHEN** a blockcommit fails with "input/output error" (not space-classified)
- **THEN** Core raises `RuntimeError` as before (VM-level abort)
- **AND** no deferred entry is created
