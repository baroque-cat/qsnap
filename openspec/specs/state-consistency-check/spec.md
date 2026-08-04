# State Consistency Check

## Purpose

Provides a `qsnap check --state` command that cross-references recorded snapshots (with per-disk SnapshotInfo), FULL backups, and incremental dependencies against the actual files on disk, detecting and reporting phantom entries (state records pointing to non-existent files) and corrupt state files. Uses shared detector methods also used by `reconcile`.

## Requirements

### Requirement: Phantom snapshot detection in state

`Core.check_state()` SHALL delegate phantom snapshot detection to `_detect_phantom_snapshots(vm)` — a shared detector method that iterates all recorded `SnapshotInfo` entries in `IStateManager` for the VM and verifies each snapshot file exists on disk via `os.path.exists()`. Each `SnapshotInfo` has a `disk` field identifying which disk the snapshot belongs to. Entries where the file does not exist SHALL be reported as phantom snapshots with status `"stale"`. The check SHALL NOT automatically remove phantom entries.

#### Scenario: All snapshot files exist — clean state

- **WHEN** `qsnap check --state` is run
- **AND** all recorded snapshots across all disks have corresponding files on disk
- **THEN** no phantom entries are reported
- **AND** status is `"ok"`

#### Scenario: Phantom snapshot detected — reported but not auto-cleaned

- **WHEN** `qsnap check --state` is run
- **AND** a recorded snapshot's file does not exist on disk
- **THEN** the snapshot is reported as phantom with path and VM name
- **AND** status includes `"stale_snapshots"`
- **AND** the phantom entry is NOT automatically removed

### Requirement: Phantom FULL backup detection in state

`Core.check_state()` SHALL delegate phantom FULL detection to `_detect_phantom_fulls(vm)` — a shared detector method that iterates all recorded FULL backups and verifies each FULL file exists on disk. Phantom FULLs SHALL be reported with status `"stale_fulls"`.

#### Scenario: Phantom FULL detected

- **WHEN** state records a FULL backup whose file is missing on disk
- **THEN** `check_state` reports it under `"stale_fulls"` without deleting anything

### Requirement: Orphaned incremental dependency detection

`Core.check_state()` SHALL delegate stale dependency detection to `_detect_stale_deps(vm)` — a shared detector method that iterates all recorded incremental→FULL dependencies and verifies both files exist on disk. Dependencies where either file is missing SHALL be reported.

#### Scenario: Stale dependency detected

- **WHEN** an incremental→FULL dependency record references a file that is missing on disk
- **THEN** `check_state` reports the stale dependency without deleting anything

### Requirement: State file integrity check

`Core.check_state()` SHALL verify that state JSON files are readable and parseable. Corrupted or unreadable state files SHALL be reported with status `"corrupt_state"`.

#### Scenario: Corrupt state file reported

- **WHEN** a VM's state JSON file is malformed or unreadable
- **THEN** `check_state` reports it under `"corrupt_state"`

### Requirement: Orphan checkpoint auto-cleanup parameter

The `_detect_orphan_checkpoints()` method SHALL accept an `auto_cleanup: bool = False` keyword parameter. When `auto_cleanup=True`, the method SHALL delete orphaned checkpoints via `virsh checkpoint-delete --metadata` through `IShell.run()`.

#### Scenario: Auto-cleanup disabled by default

- **WHEN** `_detect_orphan_checkpoints(vm_config)` is called without `auto_cleanup`
- **THEN** the method SHALL only report orphaned checkpoints without deleting them

#### Scenario: Auto-cleanup enabled deletes orphans

- **WHEN** `_detect_orphan_checkpoints(vm_config, auto_cleanup=True)` is called and orphaned checkpoints are detected
- **THEN** the method SHALL execute `virsh checkpoint-delete --metadata --domain <vm> <checkpoint>` for each orphan
- **AND** SHALL log INFO for successful deletions and WARNING for failures

#### Scenario: Auto-cleanup failure is non-fatal

- **WHEN** `virsh checkpoint-delete` fails for a specific checkpoint
- **THEN** the method SHALL log a WARNING with the error and continue to the next checkpoint
- **AND** SHALL NOT raise an exception

### Requirement: Reconcile uses auto-cleanup for orphan checkpoints

The `Core.reconcile()` method SHALL call `_detect_orphan_checkpoints(vm_config, auto_cleanup=True)` for each VM being reconciled.

#### Scenario: Reconcile auto-deletes orphan checkpoints

- **WHEN** `core.reconcile()` is called and orphaned checkpoints exist for a VM
- **THEN** the checkpoints SHALL be deleted via `virsh checkpoint-delete --metadata`
- **AND** the count of deleted checkpoints SHALL be recorded in `ReconcileResult.orphan_checkpoints_deleted`

### Requirement: Startup validation does NOT auto-delete checkpoints

The `_validate_state_at_startup()` method SHALL NOT call `_detect_orphan_checkpoints()` with `auto_cleanup=True`. Orphan checkpoint cleanup SHALL only happen via explicit `qsnap reconcile` invocation.

#### Scenario: Startup validation leaves orphan checkpoints

- **WHEN** the pipeline starts and orphaned checkpoints exist
- **THEN** the system SHALL NOT delete them
- **AND** SHALL leave cleanup to the explicit `reconcile` command

### Requirement: Broken backing chain detection in check --state

`Core.check_state()` SHALL delegate broken chain detection to `_detect_broken_chains(vm)` — a shared detector method. For each non-FULL backup file (filename not containing `.FULL.`), the method SHALL run `scan_backing_chain()` and check whether the chain is intact. Files where the scan fails SHALL be reported as `broken_chains` with the backup name and target path. The status string SHALL include `"broken_chains"` when any broken chains are detected. FULL backups (standalone files with no backing) SHALL be skipped — they have no backing chain to validate.

The `StateCheckResult` dataclass SHALL include a `broken_chains: list[str]` field (defaulting to an empty list) containing human-readable descriptions of each broken chain (format: `"{backup_name} (target: {target_path})"`).

#### Scenario: Broken backing chain detected

- **WHEN** `qsnap check --state` is run
- **AND** a non-FULL backup file at a target has a broken backing chain (its backing file was deleted)
- **THEN** the backup is reported in `broken_chains`
- **AND** the status string includes `"broken_chains"`

#### Scenario: All backing chains intact — clean state

- **WHEN** `qsnap check --state` is run
- **AND** all non-FULL backup files have intact backing chains
- **THEN** `broken_chains` is an empty list
- **AND** the status string does NOT include `"broken_chains"`

#### Scenario: FULL backups skipped in chain validation

- **WHEN** `qsnap check --state` is run
- **AND** a FULL backup exists at the target
- **THEN** the FULL backup is NOT checked for backing-chain integrity (it has no backing file)
- **AND** only non-FULL backups are validated

### Requirement: check_state uses shared detection methods from Core

`Core.check_state()` SHALL delegate phantom snapshot detection, phantom FULL detection, stale dependency detection, and broken chain detection to the same shared private detector methods used by `Core.reconcile()`: `_detect_phantom_snapshots(vm)`, `_detect_phantom_fulls(vm)`, `_detect_stale_deps(vm)`, and `_detect_broken_chains(vm)`. `check_state()` SHALL consume the returned data to format its `StateCheckResult` output (building status strings like `"stale_snapshots"`, `"stale_fulls"`, `"stale_deps"`, `"broken_chains"`). The detection logic SHALL be identical between `check_state()` and `reconcile()` — only the downstream action differs (reporting vs. repair).

#### Scenario: check_state phantom snapshot detection uses shared detector

- **WHEN** `check_state(vm_filter)` is called for a VM with phantom snapshots
- **THEN** `_detect_phantom_snapshots(vm)` is called and returns a list of phantom `SnapshotInfo`
- **AND** `check_state()` formats them as `"stale_snapshots"` status part with file paths

#### Scenario: check_state phantom FULL detection uses shared detector

- **WHEN** `check_state(vm_filter)` is called for a VM with phantom FULLs
- **THEN** `_detect_phantom_fulls(vm)` is called and returns a list
- **AND** `check_state()` formats them as `"stale_fulls"` status part

#### Scenario: check_state stale dependency detection uses shared detector

- **WHEN** `check_state(vm_filter)` is called for a VM with stale deps
- **THEN** `_detect_stale_deps(vm)` is called and returns a list
- **AND** `check_state()` formats them as `"stale_deps"` status part

#### Scenario: check_state broken chain detection uses shared detector

- **WHEN** `check_state(vm_filter)` is called for a VM with broken backup chains
- **THEN** `_detect_broken_chains(vm)` is called and returns a list
- **AND** `check_state()` formats them as `"broken_chains"` status part

#### Scenario: check_state and reconcile produce identical detection results

- **WHEN** both `check_state(vm_filter)` and `reconcile(vm_filter)` are called on the same VM state
- **THEN** the phantom snapshots, FULLs, stale deps, and broken chains detected are identical
- **AND** only the downstream actions differ (reporting vs. repair)
