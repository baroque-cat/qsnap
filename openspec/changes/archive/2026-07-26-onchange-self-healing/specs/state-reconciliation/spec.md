## ADDED Requirements

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

The system SHALL provide a `ReconcileResult` frozen dataclass in `models/results.py` with the following fields: `vm_name: str`, `phantom_snapshots_removed: int`, `phantom_fulls_removed: int`, `stale_deps_removed: int`, `baselines_cleared: int`, `orphan_checkpoints_deleted: int`, `orphan_files_removed: int`, `errors: list[str]`.

#### Scenario: ReconcileResult is frozen

- **WHEN** a `ReconcileResult` is constructed
- **THEN** all fields SHALL be immutable (frozen dataclass)
- **AND** `errors` SHALL default to an empty list
