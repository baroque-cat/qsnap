# Deferred Operations

## Purpose

Per-disk deferred blockcommit operations — when AppArmor/SELinux block virsh blockcommit, or when the VM power state prevents immediate commit, snapshots are queued per disk in IStateManager and retried on VM shutdown.

## Requirements

### Requirement: Per-disk deferred blockcommit queue in IStateManager

`IStateManager` SHALL provide methods for managing a deferred blockcommit queue: `get_deferred_operations(vm_name) -> list[DeferredBlockcommit]`, `add_deferred_blockcommit(vm_name, disk, snapshots, reason)`, `clear_deferred_operations(vm_name)`, `update_deferred_warning(vm_name, index, timestamp)`. `DeferredBlockcommit` SHALL be a dataclass with fields `snapshots: list[str]`, `reason: str`, `since: datetime`, `disk: str`, and `last_warned_at: datetime | None` (default `None`). Each entry belongs to a specific disk target (e.g. `"vda"`) identified by the `disk` field.

The `reason` field SHALL accept any string value. Known values include `"apparmor"`, `"selinux"`, `"vm_running"`, and `"active_layer"`. The `"vm_running"` reason is used when Core defers blockcommit because the VM is not in "shut off" state (running or paused). The `"active_layer"` reason is used when Core defers the XML-referenced tip overlay of an inactive domain, which must never be committed or deleted offline (the domain would become unbootable).

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

An entry SHALL be removed from the queue only when all of its snapshots have been committed. Failed entries remain queued (unchanged). Stale entries whose snapshots no longer exist in `IStateManager` or whose disk is no longer configured SHALL be dropped. `deep_verify=vm_config.blockcommit_deep_verify` SHALL be passed to the executor on the drain path. After any successful drain via the `qemu-img` executor, Core SHALL refresh the domain XML `<backingStore>`.

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
