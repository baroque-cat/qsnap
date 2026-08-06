# Deferred Operations

## Purpose

Per-disk deferred blockcommit operations — when AppArmor/SELinux block virsh blockcommit, or when the VM power state prevents immediate commit, snapshots are queued per disk in IStateManager and retried on VM shutdown.

## Requirements

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

### Requirement: Blockcommit failure detected via shared MAC helper

The lifecycle managers SHALL detect MAC denials via the shared `detect_mac_denial` helper in `qsnap/utils/mac.py`. On detection, the manager SHALL return `CommitResult(success=False, committed_snapshot="", error="blocked by apparmor|selinux")`. Core SHALL detect the error string and record the failed snapshots as per-disk deferred operations via `add_deferred_blockcommit(vm_name, disk, snapshots, reason)`.

#### Scenario: AppArmor blocks blockcommit — deferred per disk

- **WHEN** `virsh blockcommit` stderr matches AppArmor patterns (detected by `detect_mac_denial`)
- **THEN** `CommitResult` is returned with error containing "apparmor"
- **AND** Core adds a deferred entry with `disk=<affected_disk>` and `reason="apparmor"`

#### Scenario: SELinux blocks blockcommit

- **WHEN** `virsh blockcommit` stderr matches SELinux patterns (detected by `detect_mac_denial`)
- **THEN** `CommitResult` is returned with error containing "selinux"

#### Scenario: Normal virsh failure (not MAC-related)

- **WHEN** `virsh blockcommit` stderr contains "No such file or directory"
- **THEN** `CommitResult(success=False)` is returned with the original error — no deferral

### Requirement: State-adaptive per-disk drain of the deferred queue

`Core._check_deferred_operations()` SHALL iterate each deferred entry, resolve its disk via `vm_config.get_disk(entry.disk)`, and call `_plan_blockcommit(vm_config, entry.disk, snapshots)` to select the execution strategy from the *current* VM state (same fork as the main blockcommit path):

| VM state | `lifecycle_mode` | Drain behavior |
|---|---|---|
| shut off | any | Executor = `QemuImgCommitManager`. Per entry: commit all snapshots except the XML-referenced tip; on success remove committed snapshots from `IStateManager`; if a remainder (tip) exists, re-queue it with the entry's **original** reason and disk |
| running | `virsh` | Executor = `BlockCommitManager`. Per entry: commit snapshots that are not the current active layer; re-queue any remainder with the original reason and disk |
| running | `qemu-img` | Skip all entries (offline-only mode) |
| paused / other | any | Skip all entries |
| domstate failed | any | Skip all entries (conservative) |
| dry-run (any) | any | Plan only: no execution, no state writes (see below) |

An entry SHALL be removed from the queue only when all of its snapshots have been committed. Failed entries remain queued (unchanged). Stale entries whose snapshots no longer exist in `IStateManager` or whose disk is no longer configured SHALL be dropped. `deep_verify=vm_config.blockcommit_deep_verify` SHALL be passed to the executor on the drain path. After any successful drain via the `qemu-img` executor, Core SHALL refresh the domain XML `<backingStore>`.

In dry-run mode, `_check_deferred_operations()` SHALL NOT execute any blockcommit, SHALL NOT remove or re-queue entries, SHALL NOT remove snapshots from state, and SHALL NOT refresh domain XML. The read-only plan (`_plan_blockcommit()`, including its `virsh domstate` call) SHALL still be computed so the prediction reflects the VM state; Core SHALL log a per-disk prediction of the would-be drain (committable snapshots, and the deferrable remainder when the plan splits) and record a prediction entry. When the plan is `None` (domstate failed), the prediction SHALL state that a drain would be attempted with the VM state unknown.

#### Scenario: Drain on shut-off VM uses qemu-img with per-disk base image

- **WHEN** the VM is shut off and a queued entry for disk `vda` contains snapshots `["s1", "s2"]` where `s2` is the XML-referenced tip
- **AND** `vm_config.get_disk("vda")` returns `DiskConfig(base_image=Path("/data/vm_vda.qcow2"))`
- **THEN** `s1` is committed via `QemuImgCommitManager` with `disk="vda"` and `base_image=Path("/data/vm_vda.qcow2")`
- **AND** `s1` is removed from `IStateManager`
- **AND** the entry is re-queued with snapshots `["s2"]`, its original reason, and disk `"vda"`

#### Scenario: Drain on running VM in virsh mode commits formerly-active layers

- **WHEN** the VM is running, `lifecycle_mode = "virsh"`, and a queued entry's snapshots for disk `vda` are all below the current active layer
- **THEN** they are committed via `BlockCommitManager` with `disk="vda"` and the disk's base image
- **AND** the entry is removed from the queue

#### Scenario: No drain on running VM in qemu-img mode

- **WHEN** the VM is running and `lifecycle_mode = "qemu-img"`
- **THEN** no queued entry is executed and the queue is unchanged

#### Scenario: Deferred blockcommit still fails on retry

- **WHEN** a deferred blockcommit fails again on a shut-off VM (e.g. disk error)
- **THEN** the error is logged and the deferred entry remains for the next run
- **AND** pipeline continues (does not abort)

#### Scenario: Stale entry with unconfigured disk is dropped

- **WHEN** an entry references disk `vdb` but `vm_config.get_disk("vdb")` returns `None`
- **THEN** the entry is dropped from the queue with a WARNING log

#### Scenario: Drain uses per-disk base image

- **WHEN** draining an entry for disk `vdb` with `disk_cfg.base_image = Path("/data/vm_vdb.qcow2")`
- **THEN** the lifecycle manager is called with `disk="vdb"` and `base_image=Path("/data/vm_vdb.qcow2")`

#### Scenario: Dry-run predicts the drain without executing it

- **WHEN** dry-run is active and a queued entry for disk `vda` contains snapshots `["s1", "s2"]`
- **THEN** `_plan_blockcommit()` is evaluated read-only and a per-disk prediction naming the committable snapshots is logged
- **AND** no lifecycle manager `blockcommit()` is called
- **AND** the deferred queue, snapshot state, and domain XML are unchanged

#### Scenario: Dry-run with unknown VM state

- **WHEN** dry-run is active and `virsh domstate` fails for a VM with queued deferred entries
- **THEN** a prediction is logged stating that a drain would be attempted with the VM state unknown
- **AND** nothing is executed or written

### Requirement: Blockcommit space errors deferred instead of aborting
When a blockcommit (live `virsh blockcommit` or offline `qemu-img commit`/rebase) fails and the error is space-classified (`is_space_error` returns `True`), Core SHALL record the failed snapshots as a per-disk deferred operation with `reason="enospc"` via `add_deferred_blockcommit` and SHALL NOT raise `RuntimeError`. The snapshot state records SHALL remain intact. The entry SHALL be drained by the standard state-adaptive drain path on subsequent runs and SHALL count toward the standard deferred threshold monitoring (warn/crit count and age).

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
