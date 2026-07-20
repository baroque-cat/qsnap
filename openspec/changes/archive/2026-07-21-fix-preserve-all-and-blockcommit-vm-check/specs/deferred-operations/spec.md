## MODIFIED Requirements

### Requirement: Deferred blockcommit queue in IStateManager

`IStateManager` SHALL provide methods for managing a deferred blockcommit queue: `get_deferred_operations(vm_name) -> list[DeferredBlockcommit]`, `add_deferred_blockcommit(vm_name, snapshots, reason)`, `clear_deferred_operations(vm_name)`, `update_deferred_warning(vm_name, index, timestamp)`. `DeferredBlockcommit` SHALL be a frozen dataclass with fields `snapshots: list[str]`, `reason: str`, `since: datetime`, `last_warned_at: datetime | None` (default `None`).

The `reason` field SHALL accept any string value. Known values include `"apparmor"`, `"selinux"`, `"vm_running"`, and `"active_layer"`. The `"vm_running"` reason is used when Core defers blockcommit because the VM is not in "shut off" state (or is paused). The `"active_layer"` reason is used when Core defers the XML-referenced tip overlay of an inactive domain, which must never be committed or deleted offline (the domain would become unbootable); such entries become drainable once the snapshot is no longer the tip (a newer snapshot exists and the VM runs).

#### Scenario: Add and retrieve deferred blockcommit
- **WHEN** `add_deferred_blockcommit("vm1", ["snap1.qcow2"], "apparmor")` is called
- **THEN** `get_deferred_operations("vm1")` returns a list containing one `DeferredBlockcommit` with snapshots=["snap1.qcow2"], reason="apparmor", last_warned_at=None

#### Scenario: Add deferred blockcommit with vm_running reason
- **WHEN** `add_deferred_blockcommit("vm1", ["snap1.qcow2", "snap2.qcow2"], "vm_running")` is called
- **THEN** `get_deferred_operations("vm1")` returns a list containing one `DeferredBlockcommit` with reason="vm_running"

#### Scenario: Add deferred blockcommit with active_layer reason
- **WHEN** `add_deferred_blockcommit("vm1", ["snap3.qcow2"], "active_layer")` is called
- **THEN** `get_deferred_operations("vm1")` returns a list containing one `DeferredBlockcommit` with reason="active_layer"

#### Scenario: Clear deferred operations
- **WHEN** `clear_deferred_operations("vm1")` is called after adding two deferred items
- **THEN** `get_deferred_operations("vm1")` returns an empty list

#### Scenario: No deferred operations for VM
- **WHEN** `get_deferred_operations("vm_new")` is called for a VM with no deferred state
- **THEN** the method returns an empty list

#### Scenario: last_warned_at persists across state round-trip
- **WHEN** a deferred blockcommit with `last_warned_at=datetime(2025, 7, 13)` is written to state and read back
- **THEN** `last_warned_at` equals `datetime(2025, 7, 13)`

### Requirement: State-adaptive drain of the deferred queue

`Core._check_deferred_operations()` SHALL select the execution strategy from the *current* VM state rather than from `vm_config.lifecycle_mode` alone, using the same fork decision as the main blockcommit path:

| VM state | `lifecycle_mode` | Drain behavior |
|---|---|---|
| shut off | any | Executor = `QemuImgCommitManager`. Per entry: commit all snapshots except the XML-referenced tip; on success remove committed snapshots from `IStateManager`; if a remainder (tip) exists, re-queue it with the entry's **original** reason |
| running | `virsh` | Executor = `BlockCommitManager`. Per entry: commit snapshots that are not the current active layer; re-queue any remainder with the original reason |
| running | `qemu-img` | Skip all entries (offline-only mode) |
| paused / other | any | Skip all entries |
| domstate failed | any | Skip all entries (conservative) |

An entry SHALL be removed from the queue only when all of its snapshots have been committed. Failed entries remain queued (unchanged). Stale entries whose snapshots no longer exist in `IStateManager` are dropped (previously they were re-queued indefinitely). `deep_verify=vm_config.blockcommit_deep_verify` SHALL be passed to the executor on the drain path (unchanged). Threshold monitoring (`deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age`) is unchanged and applies to all reasons. After any successful drain via the `qemu-img` executor, Core SHALL refresh the domain XML `<backingStore>` as on the main blockcommit path (see `core-orchestrator`), so the domain stays bootable.

#### Scenario: Drain on shut-off VM uses qemu-img and keeps tip-only remainder
- **WHEN** the VM is shut off and a queued entry contains snapshots `["s1", "s2"]` where `s2` is the XML-referenced tip
- **THEN** `s1` is committed via `QemuImgCommitManager` and removed from `IStateManager`
- **AND** the entry is re-queued with snapshots `["s2"]` and its original reason

#### Scenario: Drain on running VM in virsh mode commits formerly-active layers
- **WHEN** the VM is running, `lifecycle_mode = "virsh"`, and a queued entry's snapshots are all below the current active layer
- **THEN** they are committed via `BlockCommitManager` and the entry is removed from the queue

#### Scenario: No drain on running VM in qemu-img mode
- **WHEN** the VM is running and `lifecycle_mode = "qemu-img"`
- **THEN** no queued entry is executed and the queue is unchanged
