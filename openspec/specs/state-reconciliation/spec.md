# State Reconciliation

## Purpose

Provides `qsnap reconcile` — an active state-vs-disk repair command that deletes stale state entries, clears stale baselines, deletes orphaned libvirt checkpoints, and deletes orphaned files on disk that are not tracked in state. Unlike `qsnap check --state` (read-only), `reconcile` actively fixes inconsistencies in both directions.

## Requirements

### Requirement: Reconcile command actively repairs state

The system SHALL provide a `qsnap reconcile` CLI subcommand that actively repairs state-vs-disk inconsistencies. Unlike `qsnap check --state` (read-only), `reconcile` SHALL delete stale state entries, clear stale baselines, delete orphaned libvirt checkpoints, and delete orphaned files on disk that are not tracked in state.

#### Scenario: Reconcile removes phantom FULLs with cascade cleanup

- **WHEN** `qsnap reconcile` is invoked and a FULL backup record exists in `_full_backups.json` whose file does not exist on disk
- **THEN** the system SHALL remove the FULL record from `_full_backups.json`, remove all linked incremental dependencies from `_dependencies.json`, and log a WARNING with the count of cleaned dependency records

#### Scenario: Reconcile clears stale last_backup_allocation

- **WHEN** `qsnap reconcile` is invoked and `last_backup_allocation` exists in `_target_state.json` for a target that has no FULL backup records (all removed as phantoms)
- **THEN** the system SHALL clear the `last_backup_allocation` entry and log an INFO message

#### Scenario: Reconcile removes phantom snapshots

- **WHEN** `qsnap reconcile` is invoked and a snapshot record exists in `{vm_name}.json` whose file does not exist on disk
- **THEN** the system SHALL remove the snapshot record from state and log a WARNING

#### Scenario: Reconcile removes stale incremental dependencies

- **WHEN** `qsnap reconcile` is invoked and an incremental dependency record exists in `_dependencies.json` whose incremental file does not exist on disk
- **THEN** the system SHALL remove the dependency record and log a WARNING

#### Scenario: Reconcile deletes orphaned checkpoints

- **WHEN** `qsnap reconcile` is invoked and a libvirt checkpoint with `qsnap-` prefix exists whose target hash does not match any configured target for the VM
- **THEN** the system SHALL delete the checkpoint via `virsh checkpoint-delete --metadata` and log an INFO message

#### Scenario: Reconcile dry-run mode

- **WHEN** `qsnap reconcile --dry-run` is invoked
- **THEN** the system SHALL report what would be fixed without making any changes to state files or deleting any checkpoints
- **AND** the output SHALL list each item that would be removed/cleared

#### Scenario: Reconcile returns structured result

- **WHEN** `qsnap reconcile` completes for a VM
- **THEN** the system SHALL return a `ReconcileResult` with counts of: phantom_snapshots_removed, phantom_fulls_removed, stale_deps_removed, baselines_cleared, orphan_checkpoints_deleted, orphan_files_removed, and a list of errors

#### Scenario: Reconcile with VM filter

- **WHEN** `qsnap reconcile <vm_name>` is invoked with a VM name filter
- **THEN** the system SHALL only reconcile state for VMs matching the filter
- **AND** SHALL skip VMs that do not match

#### Scenario: Reconcile removes orphan files on target

- **WHEN** `qsnap reconcile` is invoked and a `.qcow2` file exists on a target directory that is not tracked in `_full_backups.json` or `_dependencies.json` and matches the qsnap naming pattern (`{vm_name}.*`)
- **THEN** the system SHALL delete the file from the target and log a WARNING
- **AND** the count SHALL be recorded in `ReconcileResult.orphan_files_removed`
- **AND** after deletion, the system SHALL attempt to clean up any stale dependency records by calling `_resolve_chain_full_anchor(backup.path)` and, if an anchor is found, calling `IStateManager.remove_incremental_dependency(target_path, backup.name, anchor)`

#### Scenario: Reconcile skips non-qsnap files on target

- **WHEN** `qsnap reconcile` is invoked and a `.qcow2` file exists on a target directory that does not match the qsnap naming pattern (`{vm_name}.*`)
- **THEN** the system SHALL NOT delete the file and SHALL log a WARNING

#### Scenario: Reconcile removes orphan snapshot files

- **WHEN** `qsnap reconcile` is invoked and a `.qcow2` file exists in the snapshot directory that is not tracked in `{vm_name}.json`
- **THEN** the system SHALL delete the file and log a WARNING
- **AND** the count SHALL be recorded in `ReconcileResult.orphan_files_removed`

#### Scenario: Reconcile orphan file cleanup is non-fatal

- **WHEN** an error occurs during orphan file detection (e.g., target directory not accessible)
- **THEN** the system SHALL log a WARNING, record the error in `ReconcileResult.errors`, and continue with other reconciliation steps

### Requirement: ReconcileResult dataclass

The system SHALL provide a `ReconcileResult` frozen dataclass in `models/results.py` with the following fields: `vm_name: str`, `phantom_snapshots_removed: int`, `phantom_fulls_removed: int`, `stale_deps_removed: int`, `baselines_cleared: int`, `orphan_checkpoints_deleted: int`, `orphan_files_removed: int`, `errors: list[str]`, `broken_chains: list[str]`.

#### Scenario: ReconcileResult is frozen

- **WHEN** a `ReconcileResult` is constructed
- **THEN** all fields SHALL be immutable (frozen dataclass)
- **AND** `errors` SHALL default to an empty list

### Requirement: Broken backing chain detection in reconcile

`Core.reconcile()` SHALL detect broken backing chains on backup files at each target before classifying them as orphans. For each non-FULL backup file (filename not containing `.FULL.`), the method SHALL run `qemu-img info --force-share --backing-chain --output=json <path>` via `IShell.run()` and check whether the command succeeds. Files where the command fails SHALL be logged with a WARNING message indicating a broken backing chain was detected and that the file will be classified as an orphan and deleted. The `ReconcileResult` dataclass SHALL include a `broken_chains: list[str]` field (defaulting to an empty list) containing the names of backups with broken backing chains.

#### Scenario: Reconcile detects broken chain before orphan classification
- **WHEN** `qsnap reconcile` is invoked
- **AND** a non-FULL backup file at a target has a broken backing chain
- **THEN** a WARNING is logged indicating the broken chain
- **AND** the backup name is added to `ReconcileResult.broken_chains`
- **AND** the file proceeds through normal orphan classification (if not tracked in state, it is deleted)

#### Scenario: Reconcile with intact chains — no broken_chains
- **WHEN** `qsnap reconcile` is invoked
- **AND** all non-FULL backup files have intact backing chains
- **THEN** `ReconcileResult.broken_chains` is an empty list

#### Scenario: Reconcile dry-run reports broken chains without deletion
- **WHEN** `qsnap reconcile --dry-run` is invoked
- **AND** a non-FULL backup file at a target has a broken backing chain
- **THEN** a WARNING is logged indicating the broken chain
- **AND** the backup name is added to `ReconcileResult.broken_chains`
- **AND** the file is NOT deleted (dry-run mode)
