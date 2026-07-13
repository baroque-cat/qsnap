## ADDED Requirements

### Requirement: Core.list_snapshots()
`Core.list_snapshots(vm_filter=None)` SHALL return a dictionary mapping VM names to their recorded snapshots (from `IStateManager`). Snapshots SHALL be sorted by timestamp ascending.

#### Scenario: List snapshots for all VMs
- **WHEN** `core.list_snapshots()` is called and 2 VMs have snapshots
- **THEN** the result is `{"vm1": [SnapshotInfo, ...], "vm2": [SnapshotInfo, ...]}` sorted oldest-first

#### Scenario: List snapshots for filtered VM
- **WHEN** `core.list_snapshots(vm_filter="vm1")` is called
- **THEN** only "vm1" snapshots are returned

### Requirement: Core.list_backups()
`Core.list_backups(vm_filter=None)` SHALL return a dictionary mapping VM names to their existing backups (from `IBackupProvider.list()` for each target). Results SHALL be sorted by timestamp ascending.

#### Scenario: List backups for a VM with one target
- **WHEN** `core.list_backups()` is called and "vm1" has 3 backups on its target
- **THEN** the result contains 3 BackupInfo entries sorted oldest-first

#### Scenario: List backups when no backups exist
- **WHEN** `core.list_backups()` is called and no backups have been created
- **THEN** the result is an empty list per VM

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
