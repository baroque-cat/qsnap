## MODIFIED Requirements

### Requirement: Reconcile removes orphan files on target

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

#### Scenario: Reconcile cleans dependency records on orphan deletion

- **WHEN** `qsnap reconcile` deletes an orphan backup file from a target
- **AND** the orphan file had a resolvable FULL anchor in its backing chain
- **THEN** the system SHALL call `remove_incremental_dependency(target_path, orphan_name, anchor)` to clean the stale dependency record
- **AND** if no anchor is resolvable (broken chain or standalone file), no dependency cleanup is performed

## ADDED Requirements

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
