## ADDED Requirements

### Requirement: stats command summarizes snapshots and backups per VM

The `qsnap stats [vm...]` command SHALL print one row per VM with the columns `vm`, `snapshots`, `snapshot_size`, `backups`, `backup_size`. Data sources SHALL be `Core.list_snapshots(vm_filter)` and `Core.list_backups(vm_filter)`; `snapshots`/`backups` are the record counts and `snapshot_size`/`backup_size` are the sums of `SnapshotInfo.allocation`. The VM scope SHALL be the union of the two config-driven listings — only VMs configured in TOML appear (VMs with no records appear with zero counts). Output SHALL respect the global `--format` flag.

#### Scenario: Stats row per configured VM
- **WHEN** `qsnap stats` is executed and "vm1" has 2 snapshots (allocations 1000, 2000) and 1 backup (allocation 5000)
- **THEN** the row for "vm1" shows `snapshots=2`, `snapshot_size=3000`, `backups=1`, `backup_size=5000`

#### Scenario: Stats scope limited to configured VMs
- **WHEN** `qsnap stats` is executed
- **THEN** only VMs present in the TOML configuration appear in the output

## MODIFIED Requirements

### Requirement: Core.list_backups()
`Core.list_backups(vm_filter=None, tree=False)` SHALL return a dictionary mapping VM names to their backups (from `IBackupProvider.list()` for each target). The listing is config-driven: keys come from the configured VMs (via `_filter_vms`), so every selected VM appears in the result — with an empty list `{vm_name: []}` when it has no backups — and VMs not present in TOML never appear. Each `SnapshotInfo` SHALL carry a `disk` field. Results SHALL be sorted by timestamp ascending.

When `tree=False` (default), returns a flat list of `(target_path, backup)` tuples per VM sorted by timestamp, so callers can tell which target each backup belongs to: `{vm_name: [(target_path, SnapshotInfo), ...]}`.

When `tree=True`, returns per-VM, per-target, per-disk chain grouping: `{vm_name: [(target_path, {chain_id: [backups]})]}`. Chains are grouped by FULL anchor via `_group_backups_by_chain()`. Orphans (no FULL anchor) are grouped under the `"__orphan__"` key. Within each target, chains are grouped by disk.

#### Scenario: List backups for a VM with one target
- **WHEN** `core.list_backups()` is called and "vm1" has 3 backups on its target
- **THEN** the result contains 3 `(target_path, backup)` entries sorted oldest-first, each backup with a `disk` field

#### Scenario: List backups when no backups exist
- **WHEN** `core.list_backups()` is called and no backups have been created
- **THEN** the result is `{vm_name: []}` — every configured VM maps to an empty list

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
