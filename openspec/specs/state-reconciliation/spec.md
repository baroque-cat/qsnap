# State Reconciliation

## Purpose

Provides `qsnap reconcile` — an active state-vs-disk repair command that deletes stale state entries, clears stale baselines, deletes orphaned libvirt checkpoints, and deletes orphaned files on disk that are not tracked in state. Unlike `qsnap check --state` (read-only), `reconcile` actively fixes inconsistencies in both directions.

## Requirements

### Requirement: Reconcile command actively repairs state

The system SHALL provide a `qsnap reconcile` CLI subcommand that actively repairs state-vs-disk inconsistencies. Unlike `qsnap check` (read-only), `reconcile` SHALL fix inconsistencies by: (a) removing phantom state entries (state has record, file missing), (b) supplementing state from disk+XML reality (file exists on disk and in domain XML but not in state → record in state), (c) refreshing stale domain XML via `_refresh_domain_backing_store()`, (d) deleting orphan files not referenced by state or domain XML, (e) deleting orphaned libvirt checkpoints, (f) clearing stale baselines. Reconcile SHALL NOT perform unsafe `qemu-img rebase -u` on broken chains — it SHALL only log CRITICAL and leave the chain for operator intervention. Reconcile SHALL NOT enforce retention policy (deletion of old chains is the pipeline's job).

#### Scenario: Reconcile removes phantom FULLs with cascade cleanup

- **WHEN** `qsnap reconcile` is invoked and a FULL backup record exists in `_full_backups.json` whose file does not exist on disk
- **AND** domain XML does not reference the FULL (backups are not in domain XML)
- **THEN** the system SHALL remove the FULL record from `_full_backups.json`, remove all linked incremental dependencies from `_dependencies.json`, and log a WARNING with the count of cleaned dependency records

#### Scenario: Reconcile clears stale last_backup_allocation

- **WHEN** `qsnap reconcile` is invoked and `last_backup_allocation` exists in `_target_state.json` for a target that has no FULL backup records (all removed as phantoms)
- **THEN** the system SHALL clear the `last_backup_allocation` entry and log an INFO message

#### Scenario: Reconcile removes phantom snapshots

- **WHEN** `qsnap reconcile` is invoked and a snapshot record exists in `{vm_name}.json` whose file does not exist on disk
- **AND** domain XML does not reference the snapshot (legitimately deleted via blockcommit)
- **THEN** the system SHALL remove the snapshot record from state and log a WARNING

#### Scenario: Reconcile removes stale incremental dependencies

- **WHEN** `qsnap reconcile` is invoked and an incremental dependency record exists in `_dependencies.json` whose incremental file does not exist on disk
- **THEN** the system SHALL remove the dependency record and log a WARNING

#### Scenario: Reconcile supplements state from disk+XML reality

- **WHEN** `qsnap reconcile` is invoked and a `.qcow2` file exists on disk in the snapshot directory
- **AND** the file is NOT tracked in `{vm_name}.json` state
- **AND** domain XML references the file in `<backingStore>` (the file is part of the active chain)
- **THEN** the system SHALL call `record_snapshot()` to add the file to state
- **AND** log an INFO message: "state supplemented: <snapshot_name> recorded from disk+XML reality"
- **AND** `ReconcileResult.state_supplemented` SHALL be incremented

#### Scenario: Reconcile deletes orphan files not in state or XML

- **WHEN** `qsnap reconcile` is invoked and a `.qcow2` file exists on disk
- **AND** the file is NOT tracked in state
- **AND** domain XML does NOT reference the file (truly orphan)
- **THEN** the system SHALL delete the file via `rm -f` and log a WARNING
- **AND** `ReconcileResult.orphan_files_removed` SHALL be incremented

#### Scenario: Reconcile refreshes stale domain XML

- **WHEN** `qsnap reconcile` is invoked
- **AND** domain XML contains `<backingStore>` elements referencing files that do not exist on disk
- **AND** state and disk agree (the files were legitimately deleted)
- **THEN** the system SHALL call `_refresh_domain_backing_store()` to strip stale `<backingStore>` elements
- **AND** apply the modified XML via `virsh define`
- **AND** `ReconcileResult.xml_refreshed` SHALL be `True`
- **AND** log a WARNING: "stripped stale <backingStore> from domain XML"

#### Scenario: Reconcile does NOT auto-rebase broken chains

- **WHEN** `qsnap reconcile` is invoked
- **AND** a broken backing chain is detected (file missing from the middle of the chain)
- **THEN** the system SHALL log CRITICAL: "broken chain at <file> — blockcommit impossible, restore from backup target"
- **AND** the system SHALL NOT call `qemu-img rebase -u`
- **AND** `ReconcileResult.broken_chains` SHALL include the file name

#### Scenario: Reconcile deletes orphaned checkpoints

- **WHEN** `qsnap reconcile` is invoked and a libvirt checkpoint with `qsnap-` prefix exists whose target hash does not match any configured target for the VM
- **THEN** the system SHALL delete the checkpoint via `virsh checkpoint-delete --metadata` and log an INFO message

#### Scenario: Reconcile dry-run mode

- **WHEN** `qsnap reconcile --dry-run` is invoked
- **THEN** the system SHALL report what would be fixed without making any changes to state files, disk, or domain XML
- **AND** the output SHALL list each item that would be removed/cleared/supplemented/refreshed
- **AND** all log messages SHALL be prefixed with `[dry-run reconcile]`

#### Scenario: Reconcile returns structured result

- **WHEN** `qsnap reconcile` completes for a VM
- **THEN** the system SHALL return a `ReconcileResult` with counts of: phantom_snapshots_removed, phantom_fulls_removed, stale_deps_removed, baselines_cleared, orphan_checkpoints_deleted, orphan_files_removed, state_supplemented, xml_refreshed, allocation_fixed, broken_chains, and a list of errors

#### Scenario: Reconcile with VM filter

- **WHEN** `qsnap reconcile <vm_name>` is invoked with a VM name filter
- **THEN** the system SHALL only reconcile state for VMs matching the filter
- **AND** SHALL skip VMs that do not match

#### Scenario: Reconcile removes orphan files on target

- **WHEN** `qsnap reconcile` is invoked and a `.qcow2` file exists on a target directory that is not tracked in `_full_backups.json` or `_dependencies.json` and matches the qsnap naming pattern (`{vm_name}.*`)
- **AND** the file has a broken backing chain (not traversable to any FULL)
- **THEN** the system SHALL delete the file from the target and log a WARNING
- **AND** the count SHALL be recorded in `ReconcileResult.orphan_files_removed`

#### Scenario: Reconcile supplements state for orphan backup with intact chain

- **WHEN** `qsnap reconcile` is invoked and a `.qcow2` file exists on a target directory
- **AND** the file is NOT tracked in `_full_backups.json` or `_dependencies.json`
- **AND** `qemu-img info --backing-chain` shows the file has an intact chain to a FULL that IS tracked in state
- **THEN** the system SHALL call `record_incremental_dependency()` to add the file to state
- **AND** log an INFO: "state supplemented: <backup_name> recorded from disk reality"
- **AND** `ReconcileResult.state_supplemented` SHALL be incremented

#### Scenario: Reconcile skips non-qsnap files on target

- **WHEN** `qsnap reconcile` is invoked and a `.qcow2` file exists on a target directory that does not match the qsnap naming pattern (`{vm_name}.*`)
- **THEN** the system SHALL NOT delete the file and SHALL log a WARNING

#### Scenario: Reconcile orphan file cleanup is non-fatal

- **WHEN** an error occurs during orphan file detection (e.g., target directory not accessible)
- **THEN** the system SHALL log a WARNING, record the error in `ReconcileResult.errors`, and continue with other reconciliation steps

### Requirement: ReconcileResult dataclass

The system SHALL provide a `ReconcileResult` frozen dataclass in `models/results.py` with the following fields: `vm_name: str`, `phantom_snapshots_removed: int`, `phantom_fulls_removed: int`, `stale_deps_removed: int`, `baselines_cleared: int`, `orphan_checkpoints_deleted: int`, `orphan_files_removed: int`, `state_supplemented: int`, `xml_refreshed: bool`, `allocation_fixed: bool`, `errors: list[str]`, `broken_chains: list[str]`.

#### Scenario: ReconcileResult is frozen

- **WHEN** a `ReconcileResult` is constructed
- **THEN** all fields SHALL be immutable (frozen dataclass)
- **AND** `errors` SHALL default to an empty list
- **AND** `state_supplemented` SHALL default to `0`
- **AND** `xml_refreshed` SHALL default to `False`
- **AND** `allocation_fixed` SHALL default to `False`

### Requirement: Broken backing chain detection in reconcile

`Core.reconcile()` SHALL detect broken backing chains on backup files at each target before classifying them as orphans. For each non-FULL backup file (filename not containing `.FULL.`), the method SHALL run `qemu-img info --force-share --backing-chain --output=json <path>` via `IShell.run()` and check whether the command succeeds. Files where the command fails SHALL be logged with a CRITICAL message indicating a broken backing chain was detected. The system SHALL NOT attempt to repair broken chains via `qemu-img rebase -u`. The `ReconcileResult` dataclass SHALL include a `broken_chains: list[str]` field (defaulting to an empty list) containing the names of backups with broken backing chains.

#### Scenario: Reconcile detects broken chain — no auto-rebase

- **WHEN** `qsnap reconcile` is invoked
- **AND** a non-FULL backup file at a target has a broken backing chain
- **THEN** a CRITICAL is logged indicating the broken chain
- **AND** the backup name is added to `ReconcileResult.broken_chains`
- **AND** the system SHALL NOT call `qemu-img rebase -u`
- **AND** the file is NOT deleted (left for operator review)

#### Scenario: Reconcile with intact chains — no broken_chains

- **WHEN** `qsnap reconcile` is invoked
- **AND** all non-FULL backup files have intact backing chains
- **THEN** `ReconcileResult.broken_chains` is an empty list

#### Scenario: Reconcile dry-run reports broken chains without deletion

- **WHEN** `qsnap reconcile --dry-run` is invoked
- **AND** a non-FULL backup file at a target has a broken backing chain
- **THEN** a CRITICAL is logged indicating the broken chain
- **AND** the backup name is added to `ReconcileResult.broken_chains`
- **AND** the file is NOT deleted (dry-run mode)
- **AND** no `qemu-img rebase` is attempted

---

# state-reconciliation — Delta Spec

## ADDED Requirements

### Requirement: Reconcile uses shared detection methods from Core

`Core.reconcile()` SHALL delegate phantom snapshot detection, phantom FULL detection, stale dependency detection, and broken chain detection to shared private detector methods on Core: `_detect_phantom_snapshots(vm)`, `_detect_phantom_fulls(vm)`, `_detect_stale_deps(vm)`, and `_detect_broken_chains(vm)`. These methods SHALL return pure data (lists of detected items) without performing any state mutation, logging, or dry-run gating. `reconcile()` SHALL consume the returned data to perform its repair actions (state mutation, XML refresh, file deletion, log warnings). The detector methods SHALL be shared with `Core.check_state()`, eliminating the current code duplication between the two methods. The detectors SHALL NOT receive pre-parsed XML data — `reconcile()` SHALL parse domain XML via `_parse_domain_xml_source_paths()` before calling detectors that need it, and pass the XML paths as parameters.

`_cross_reference_snapshots()` SHALL remain the exclusive triple-source matrix classifier (state/disk/XML → OK/phantom/stale-XML/orphan). `reconcile()` continues to use its own inline classification for the repair decision-making, but the raw data collection (file existence checks, dependency traversal) SHALL go through the shared detectors.

#### Scenario: Reconcile phantom snapshot detection uses shared detector

- **WHEN** `reconcile(vm_filter)` is called for a VM with phantom snapshots
- **THEN** `_detect_phantom_snapshots(vm)` is called and returns a list of phantom `SnapshotInfo`
- **AND** `reconcile()` processes the returned list for state removal (optionally cross-referencing with `xml_paths`)

#### Scenario: Reconcile phantom FULL detection uses shared detector

- **WHEN** `reconcile(vm_filter)` is called for a VM with phantom FULLs
- **THEN** `_detect_phantom_fulls(vm)` is called and returns a list of `(TargetConfig, FullBackupInfo)` tuples
- **AND** `reconcile()` processes the returned list for cascade cleanup

#### Scenario: Reconcile stale dependency detection uses shared detector

- **WHEN** `reconcile(vm_filter)` is called for a VM with stale dependencies
- **THEN** `_detect_stale_deps(vm)` is called and returns a list of `(dep_name, full_name, TargetConfig)` tuples
- **AND** `reconcile()` processes the returned list for dependency removal

#### Scenario: Reconcile broken chain detection uses shared detector

- **WHEN** `reconcile(vm_filter)` is called for a VM with broken backup chains
- **THEN** `_detect_broken_chains(vm)` is called and returns a list of backup names with broken chains
- **AND** `reconcile()` processes the returned list for CRITICAL logging (no auto-repair)

#### Scenario: Detectors return data only — no side effects

- **WHEN** any shared detector method is called
- **THEN** it does NOT modify `IStateManager`
- **AND** it does NOT delete files
- **AND** it does NOT log at WARNING or higher severity
- **AND** it does NOT check `self._dry_run`
