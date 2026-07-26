# State Consistency Check

## Purpose

Provides a `qsnap check --state` command that cross-references recorded snapshots, FULL backups, and incremental dependencies against the actual files on disk, detecting and reporting phantom entries (state records pointing to non-existent files) and corrupt state files.

## Requirements

### Requirement: Phantom snapshot detection in state

`Core.check_state()` SHALL iterate all recorded snapshots in `IStateManager` for each VM and verify the snapshot file exists on disk via `os.path.exists()`. Entries where the file does not exist SHALL be reported as phantom snapshots with status `"stale"`. The check SHALL NOT automatically remove phantom entries.

#### Scenario: All snapshot files exist — clean state
- **WHEN** `qsnap check --state` is run
- **AND** all recorded snapshots have corresponding files on disk
- **THEN** no phantom entries are reported
- **AND** status is `"ok"`

#### Scenario: Phantom snapshot detected — reported but not auto-cleaned
- **WHEN** `qsnap check --state` is run
- **AND** a recorded snapshot's file does not exist on disk
- **THEN** the snapshot is reported as phantom with path and VM name
- **AND** status is `"stale_snapshots"`
- **AND** the phantom entry is NOT automatically removed

### Requirement: Phantom FULL backup detection in state

`Core.check_state()` SHALL iterate all recorded FULL backups and verify each FULL file exists on disk. Phantom FULLs SHALL be reported with status `"stale_fulls"`.

### Requirement: Orphaned incremental dependency detection

`Core.check_state()` SHALL iterate all recorded incremental→FULL dependencies and verify both files exist on disk. Dependencies where either file is missing SHALL be reported.

### Requirement: State file integrity check

`Core.check_state()` SHALL verify that state JSON files are readable and parseable. Corrupted or unreadable state files SHALL be reported with status `"corrupt_state"`.

### Requirement: Orphan checkpoint auto-cleanup parameter

The `_detect_orphan_checkpoints()` method SHALL accept an `auto_cleanup: bool = False` keyword parameter. When `auto_cleanup=True`, the method SHALL delete orphaned checkpoints via `virsh checkpoint-delete --metadata` through `IShell.run()`.

#### Scenario: Auto-cleanup disabled by default

- **WHEN** `_detect_orphan_checkpoints(vm_config)` is called without `auto_cleanup`
- **THEN** the method SHALL only report orphaned checkpoints without deleting them (backward-compatible behavior)

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
