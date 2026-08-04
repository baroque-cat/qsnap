# List Commands

## Purpose

Read-only `Core.list_*` methods that surface snapshots, backups, configuration, latest snapshots, schedule, and integrity status to the CLI. All listing is disk-aware: every snapshot/backup record carries its disk target, and output is grouped or columned by disk.

## Requirements

### Requirement: Core.list_snapshots()
`Core.list_snapshots(vm_filter=None)` SHALL return a dictionary mapping VM names to their recorded snapshots (from `IStateManager`). Each `SnapshotInfo` SHALL carry a `disk` field identifying the disk target (e.g. `"vda"`). Snapshots SHALL be sorted by timestamp ascending.

#### Scenario: List snapshots for all VMs
- **WHEN** `core.list_snapshots()` is called and 2 VMs have snapshots
- **THEN** the result is `{"vm1": [SnapshotInfo(disk="vda", ...), ...], "vm2": [...]}` sorted oldest-first

#### Scenario: List snapshots for filtered VM
- **WHEN** `core.list_snapshots(vm_filter="vm1")` is called
- **THEN** only "vm1" snapshots are returned

### Requirement: Core.list_backups()
`Core.list_backups(vm_filter=None, tree=False)` SHALL return a dictionary mapping VM names to their backups (from `IBackupProvider.list()` for each target). Each `SnapshotInfo` SHALL carry a `disk` field. Results SHALL be sorted by timestamp ascending.

When `tree=False` (default), returns a flat list of `(target_path, backup)` tuples per VM sorted by timestamp, so callers can tell which target each backup belongs to: `{vm_name: [(target_path, SnapshotInfo), ...]}`.

When `tree=True`, returns per-VM, per-target, per-disk chain grouping: `{vm_name: [(target_path, {chain_id: [backups]})]}`. Chains are grouped by FULL anchor via `_group_backups_by_chain()`. Orphans (no FULL anchor) are grouped under the `"__orphan__"` key. Within each target, chains are grouped by disk.

#### Scenario: List backups for a VM with one target
- **WHEN** `core.list_backups()` is called and "vm1" has 3 backups on its target
- **THEN** the result contains 3 `(target_path, backup)` entries sorted oldest-first, each backup with a `disk` field

#### Scenario: List backups when no backups exist
- **WHEN** `core.list_backups()` is called and no backups have been created
- **THEN** the result is an empty list per VM

#### Scenario: Flat list when tree=False
- **WHEN** `core.list_backups(tree=False)` is called for a VM with two targets
- **THEN** a flat list of `(target_path, backup)` tuples sorted by timestamp is returned, each tuple carrying the target the backup belongs to

#### Scenario: Tree grouping when tree=True
- **WHEN** `core.list_backups(tree=True)` is called and a VM has 2 FULL chains with 3 incrementals each
- **THEN** backups are grouped by FULL anchor, then by disk
- **AND** each group contains the FULL and its dependent incrementals

#### Scenario: Orphan backups grouped separately
- **WHEN** `core.list_backups(tree=True)` is called and orphan backups exist (no FULL anchor)
- **THEN** orphans are grouped under a `"__orphan__"` key per disk

### Requirement: Core.list_config()
`Core.list_config()` SHALL return the list of all configured VMs from `IConfigFacade.get_vms()`. Each `VMConfig` SHALL have `disks: list[DiskConfig]` (with `target` and `base_image`). There is NO VM-level `base_image`.

#### Scenario: List configuration
- **WHEN** `core.list_config()` is called
- **THEN** all `VMConfig` objects from the parsed config are returned, each with per-disk configuration

### Requirement: Core.list_latest()
`Core.list_latest(vm_filter=None)` SHALL return a dictionary mapping VM names to a per-disk mapping of the most recent snapshot. Multi-disk: each VM maps to `{disk_target: latest_snapshot_or_None}`. Every configured disk of the VM SHALL appear in the inner mapping, with `None` when that disk has no snapshots. Each returned `SnapshotInfo` SHALL include the `disk` field.

#### Scenario: Latest snapshot found per disk
- **WHEN** `core.list_latest()` is called and "vm1" (disks vda, vdb) has 3 snapshots all on vda
- **THEN** the result is `{"vm1": {"vda": <newest SnapshotInfo>, "vdb": None}}`

#### Scenario: Independent per-disk latest
- **WHEN** `core.list_latest()` is called and both vda and vdb have snapshots
- **THEN** each disk maps to its own newest snapshot, computed independently

#### Scenario: No snapshots
- **WHEN** `core.list_latest()` is called for a VM whose disks have no snapshots
- **THEN** every configured disk maps to `None`

### Requirement: Core.print_schedule()
`Core.print_schedule(vm_filter=None)` SHALL evaluate retention policy for all snapshots and backups without executing any deletion. The result SHALL show which snapshots/backups would be kept and which would be removed.

#### Scenario: Schedule shows keep/remove decisions
- **WHEN** `core.print_schedule()` is called and a VM has 10 snapshots with policy `hourly=6`
- **THEN** 6 snapshots are marked as keep and 4 as remove, in oldest-first order

#### Scenario: Schedule does not mutate
- **WHEN** `core.print_schedule()` is called
- **THEN** no `IShell.run()` call modifies the filesystem

### Requirement: Core.check()
`Core.check(vm_filter=None)` SHALL verify backing chain integrity for each VM's snapshots by checking that each snapshot's backing file exists and the chain is not broken.

#### Scenario: Healthy backing chain
- **WHEN** `core.check()` is called and all backing files exist
- **THEN** each VM reports status "ok"

#### Scenario: Broken backing chain
- **WHEN** `core.check()` is called and a snapshot's backing file is missing
- **THEN** that snapshot reports status "broken: backing file not found"

### Requirement: Core.list_deferred() method

Core SHALL expose a `list_deferred(vm_filter=None)` method that retrieves deferred blockcommit operations from `IStateManager` and returns per-VM per-disk `DeferredSummary` objects. Each summary SHALL include: `vm_name`, `disk`, `snapshot_count`, `reason`, `age`, and `since`. Deferred entries are scoped per disk — a summary is produced for each (VM, disk) pair with pending entries.

#### Scenario: list_deferred returns per-VM per-disk summaries

- **WHEN** `core.list_deferred()` is called
- **AND** VM "vm1" has deferred operations on disk "vda" (3 snapshots) and disk "vdb" (5 snapshots)
- **THEN** two summaries are returned with `vm_name="vm1", disk="vda"` and `vm_name="vm1", disk="vdb"`

#### Scenario: list_deferred with no deferred operations

- **WHEN** `core.list_deferred()` is called and no VM has any deferred operations
- **THEN** an empty list is returned

#### Scenario: list_deferred filtered by VM name

- **WHEN** `core.list_deferred(vm_filter="vm-home")` is called
- **THEN** only summaries for "vm-home" are returned

### Requirement: CLI _print_backup_tree function
The CLI SHALL provide a `_print_backup_tree(data, vm_configs)` function in `qsnap/cli/commands.py` that displays backup chains as an indented tree grouped by VM → Target → Disk → chains. Within each target, chains are grouped by disk target. Each FULL backup is displayed with its dependent incrementals indented beneath. Orphans are shown under `(orphan)`. The function SHALL be purely visual and SHALL NOT modify any state.

#### Scenario: Backup tree output format with multi-disk
- **WHEN** `_print_backup_tree(data, vm_configs)` is called with 2 FULL chains across 2 disks
- **THEN** output shows:
  ```
  === myvm ===
  Target: /backup/myvm
    [vda]
      myvm.FULL.20260701T120000_vda_abc123.qcow2
        myvm.20260702T120000_vda_def456.qcow2
    [vdb]
      myvm.FULL.20260701T120000_vdb_xyz789.qcow2
  ```
