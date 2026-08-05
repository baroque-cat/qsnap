# Deferred Operations — Delta Spec

## MODIFIED Requirements

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
