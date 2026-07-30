## Requirements

### Requirement: Core.list_snapshots()
`Core.list_snapshots(vm_filter=None)` SHALL return a dictionary mapping VM names to their recorded snapshots (from `IStateManager`). Snapshots SHALL be sorted by timestamp ascending.

#### Scenario: List snapshots for all VMs
- **WHEN** `core.list_snapshots()` is called and 2 VMs have snapshots
- **THEN** the result is `{"vm1": [SnapshotInfo, ...], "vm2": [SnapshotInfo, ...]}` sorted oldest-first

#### Scenario: List snapshots for filtered VM
- **WHEN** `core.list_snapshots(vm_filter="vm1")` is called
- **THEN** only "vm1" snapshots are returned

### Requirement: Core.list_backups()
`Core.list_backups(vm_filter=None, tree=False)` SHALL return a dictionary mapping VM names to their existing backups (from `IBackupProvider.list()` for each target). Results SHALL be sorted by timestamp ascending.

When `tree=False` (default), returns a flat list per VM sorted by timestamp (existing behavior).

When `tree=True`, returns per-VM, per-target chain grouping: `{vm_name: [(target_path, {chain_id: [backups]})]}`. Chains are grouped by FULL anchor via `_group_backups_by_chain()`. Orphans (no FULL anchor) are grouped under the `"__orphan__"` key.

#### Scenario: List backups for a VM with one target
- **WHEN** `core.list_backups()` is called and "vm1" has 3 backups on its target
- **THEN** the result contains 3 entries sorted oldest-first

#### Scenario: List backups when no backups exist
- **WHEN** `core.list_backups()` is called and no backups have been created
- **THEN** the result is an empty list per VM

#### Scenario: Flat list when tree=False
- **WHEN** `core.list_backups(tree=False)` is called
- **THEN** a flat list of backups sorted by timestamp is returned (existing behavior)

#### Scenario: Tree grouping when tree=True
- **WHEN** `core.list_backups(tree=True)` is called and a VM has 2 FULL chains with 3 incrementals each
- **THEN** backups are grouped by FULL anchor
- **AND** each group contains the FULL and its dependent incrementals

#### Scenario: Orphan backups grouped separately
- **WHEN** `core.list_backups(tree=True)` is called and orphan backups exist (no FULL anchor)
- **THEN** orphans are grouped under a `"__orphan__"` key

### Requirement: Core.list_config()
`Core.list_config()` SHALL return the list of all configured VMs from `IConfigFacade.get_vms()`.

#### Scenario: List configuration
- **WHEN** `core.list_config()` is called
- **THEN** all VMConfig objects from the parsed config are returned

### Requirement: Core.list_latest()
`Core.list_latest(vm_filter=None)` SHALL return a dictionary mapping VM names to their most recent snapshot (by timestamp), or `None` if the VM has no snapshots.

#### Scenario: Latest snapshot found
- **WHEN** `core.list_latest()` is called and "vm1" has 3 snapshots
- **THEN** the most recent (newest by timestamp) is returned

#### Scenario: No snapshots
- **WHEN** `core.list_latest()` is called for a VM with no snapshots
- **THEN** `None` is returned for that VM

### Requirement: Core.print_schedule()
`Core.print_schedule(vm_filter=None)` SHALL evaluate retention policy for all snapshots and backups without executing any deletion. The result SHALL show which snapshots/backups would be kept and which would be removed.

#### Scenario: Schedule shows keep/remove decisions
- **WHEN** `core.print_schedule()` is called and a VM has 10 snapshots with policy `hourly=6`
- **THEN** 6 snapshots are marked as keep and 4 as remove, in oldest-first order

#### Scenario: Schedule does not mutate
- **WHEN** `core.print_schedule()` is called
- **THEN** no `IShell.run()` call modifies the filesystem (no blockcommit, no file deletion, no snapshot creation)

### Requirement: Core.check()
`Core.check(vm_filter=None)` SHALL verify backing chain integrity for each VM's snapshots by checking that each snapshot's backing file exists and the chain is not broken.

#### Scenario: Healthy backing chain
- **WHEN** `core.check()` is called and all backing files exist
- **THEN** each VM reports status "ok"

#### Scenario: Broken backing chain
- **WHEN** `core.check()` is called and a snapshot's backing file is missing
- **THEN** that snapshot reports status "broken: backing file not found"

### Requirement: Core.list_deferred() method

Core SHALL expose a `list_deferred(vm_filter=None)` method that retrieves deferred blockcommit operations from `IStateManager` for all configured VMs (or filtered VMs) and returns per-VM summaries. Each summary SHALL include: VM name, count of pending snapshots, reason, and age of the oldest deferred entry.

#### Scenario: list_deferred returns per-VM summaries

- **WHEN** `core.list_deferred()` is called
- **AND** two VMs have 3 and 5 deferred operations respectively
- **THEN** two summaries are returned with the correct counts and ages

#### Scenario: list_deferred with no deferred operations

- **WHEN** `core.list_deferred()` is called and no VM has any deferred operations
- **THEN** an empty list is returned

#### Scenario: list_deferred filtered by VM name

- **WHEN** `core.list_deferred(vm_filter="vm-home")` is called
- **THEN** only the summary for "vm-home" is returned

### Requirement: CLI _print_backup_tree function
The CLI SHALL provide a `_print_backup_tree(data, vm_configs)` function in `qsnap/cli/commands.py` that displays backup chains as an indented tree. Each target is shown with a header, FULL backups at the top level, and their dependent incrementals indented beneath. The function SHALL be purely visual and SHALL NOT modify any state.

#### Scenario: Backup tree output format
- **WHEN** `_print_backup_tree(data, vm_configs)` is called with 2 FULL chains
- **THEN** output shows:
  ```
  === myvm ===
  Target: /backup/myvm
    myvm.FULL.20260701T120000_abc123.qcow2
      myvm.20260702T120000_def456.qcow2
      myvm.20260703T120000_ghi789.qcow2
    myvm.FULL.20260704T120000_jkl012.qcow2
      myvm.20260705T120000_mno345.qcow2
  ```
