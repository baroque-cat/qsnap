## MODIFIED Requirements

### Requirement: Stale temporary file cleanup at pipeline startup
The environment validation step SHALL remove stale files left by interrupted previous runs before any pipeline operations execute. Specifically, all files matching `*.tmp` and `*.partial` in (a) `VMConfig.snapshot_dir` and (b) each `TargetConfig.path` SHALL be deleted. Additionally, any Unix socket files matching `/tmp/qsnap-backup-*.sock` SHALL be deleted. Additionally, truncated `.qcow2` files on backup targets where `qemu-img info` returns a non-zero exit code SHALL be detected and deleted as stale partial transfers.

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

#### Scenario: Truncated rsync qcow2 file detected and deleted
- **WHEN** `target.path` contains a `.qcow2` file that is NOT `*.FULL.*.qcow2` and NOT `*.tmp` or `*.partial`
- **AND** `qemu-img info --output=json` on the file returns a non-zero exit code
- **THEN** the file is identified as a partial rsync transfer and deleted
- **AND** a WARNING is logged: "Stale partial transfer detected and deleted: <path>"

#### Scenario: Valid qcow2 files are NOT deleted
- **WHEN** `target.path` contains a `.qcow2` file
- **AND** `qemu-img info --output=json` on the file returns exit code 0
- **THEN** the file is NOT deleted

#### Scenario: No stale files — no action
- **WHEN** no `.tmp`, `.partial`, stale socket files, or truncated `.qcow2` files exist
- **THEN** no files are deleted, no INFO log for cleanup is emitted
