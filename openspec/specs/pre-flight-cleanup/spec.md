## Purpose

Cleans up stale temporary files, partial transfers, and orphaned sockets left by interrupted pipeline runs before any pipeline operations execute. Also detects orphan qcow2 snapshot files not tracked in state, emitting warnings without auto-deletion.

## Requirements

### Requirement: Stale temporary file cleanup at pipeline startup
The environment validation step SHALL remove stale files left by interrupted previous runs before any pipeline operations execute. Specifically, all files matching `*.tmp` and `*.partial` in (a) `VMConfig.snapshot_dir` and (b) each `TargetConfig.path` SHALL be deleted. Additionally, any Unix socket files matching `/tmp/qsnap-backup-*.sock` SHALL be deleted.

#### Scenario: tmp files in snapshot_dir removed
- **WHEN** `snapshot_dir` contains `snap.tmp` and `snap.partial` from a crashed previous run
- **THEN** both files are deleted before any snapshot creation begins
- **AND** an INFO log is emitted with the count and paths of removed files

#### Scenario: tmp files in target directories removed
- **WHEN** `target.path` `/mnt/backup/vm` contains `backup.qcow2.tmp` from an interrupted transfer
- **THEN** the file is deleted before any new backup transfer
- **AND** an INFO log is emitted

#### Scenario: Stale NBD sockets removed
- **WHEN** `/tmp/` contains `qsnap-backup-12345.sock` from a crashed BitmapBackupProvider run
- **THEN** the socket file is deleted

#### Scenario: No stale files — no action
- **WHEN** no `.tmp`, `.partial`, or stale socket files exist
- **THEN** no files are deleted, no INFO log for cleanup is emitted

### Requirement: Orphan qcow2 detection (warning only)
The cleanup step SHALL detect `.qcow2` files in `VMConfig.snapshot_dir` whose filename matches the qsnap naming pattern (`{vm_name}.{timestamp}.qcow2`) but are NOT recorded in `IStateManager.get_snapshots()`. Such files SHALL trigger a WARNING log with their paths. They SHALL NOT be automatically deleted.

#### Scenario: Orphan snapshot detected
- **WHEN** `snapshot_dir` contains `vm.20250715T1200.qcow2` but `get_snapshots("vm")` does not list it
- **THEN** a WARNING is logged: "Orphan snapshot file detected: /path/vm.20250715T1200.qcow2"
- **AND** the file is NOT deleted

#### Scenario: All snapshots accounted for — no warning
- **WHEN** every `.qcow2` in `snapshot_dir` has a corresponding entry in `get_snapshots()`
- **THEN** no orphan WARNING is emitted

### Requirement: GlobalConfig auto_cleanup field
`GlobalConfig` SHALL include an `auto_cleanup: bool` field with default value `True`. When `True`, the stale file cleanup and orphan detection SHALL execute. When `False`, both SHALL be skipped.

#### Scenario: auto_cleanup disabled
- **WHEN** `auto_cleanup = false` and stale files exist
- **THEN** no cleanup or orphan detection occurs
- **AND** an INFO log states "auto_cleanup is disabled — skipping stale file cleanup"

### Requirement: Truncated qcow2 detection on backup targets

After cleaning `*.tmp` and `*.partial` files, `Core._preflight_cleanup()` SHALL scan backup target directories for `.qcow2` files that are NOT `*.FULL.*.qcow2`. For each candidate, run `qemu-img info --output=json` with a 10-second timeout. If the command fails, the file is a truncated rsync artifact — delete it and log WARNING.
