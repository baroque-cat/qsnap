# Backup Target Orthogonality

## Purpose

The backup (target) world is self-contained and orthogonal to the snapshot world. One backup work unit runs per disk per pipeline run, is named by its own freeze point, is baselined only against libvirt checkpoints, and is modeled with `BackupInfo` — it consumes no snapshot data and shares no state with snapshot retention or blockcommit.

## Requirements

### Requirement: Backup phase SHALL NOT consume snapshot data

The backup (target) world SHALL be self-contained. `Core._backup_target()` and everything it invokes SHALL NOT read snapshot names, snapshot timestamps, snapshot file paths, or the snapshot list from `IStateManager`. The only inputs to the backup phase SHALL be: `VMConfig`, `TargetConfig`, the backup provider's own discovery (target file listing, libvirt checkpoints), and target-scoped state (`_target_state.json`, `_full_backups.json`, `_dependencies.json`). Stale snapshot-file healing belongs to the snapshot world, not to the backup provider.

#### Scenario: Backup phase runs with zero snapshots in state

- **WHEN** a VM has no snapshots in state and no snapshot files on disk
- **AND** `backup_create = "always"` for a target
- **THEN** FULL and subsequent delta backups are created for every configured disk
- **AND** no error or skip referencing snapshots occurs

#### Scenario: Provider receives no SnapshotInfo

- **WHEN** Core invokes any `IBackupProvider` method for backup creation, listing, or deletion
- **THEN** no `SnapshotInfo` object is passed or returned anywhere in the call chain

### Requirement: One backup work unit per disk per run

`IBackupProvider.run_backup(vm_config, target, disk, *, opts) -> BackupResult` SHALL be the sole backup creation entry point. For each disk of each target, Core SHALL invoke at most one `run_backup` per pipeline run. The provider SHALL decide the kind of backup autonomously: no checkpoint exists for this VM+target+disk → FULL; a checkpoint exists → delta of dirty blocks since the newest checkpoint. The successor checkpoint SHALL be created atomically at the export's freeze point in both cases (running VMs). `transfer_missing(snapshots)` and `create_full_backup(source_snapshot)` are removed from the interface.

#### Scenario: First backup of a disk creates a FULL

- **WHEN** `run_backup` runs for a VM+target+disk with no qsnap checkpoint
- **THEN** a FULL backup is created (NBD export for running VM, `qemu-img convert` for stopped VM)
- **AND** a successor checkpoint is created atomically at the freeze point (running VM only)

#### Scenario: Subsequent backup creates one delta

- **WHEN** `run_backup` runs and a checkpoint exists for this VM+target+disk
- **THEN** exactly one delta is created from dirty blocks since the newest checkpoint
- **AND** the delta's backing file is the newest valid backup of this disk on the target
- **AND** a successor checkpoint is created atomically at the freeze point

#### Scenario: Multiple snapshots since last backup produce one delta

- **WHEN** three snapshots were created since the last backup run
- **THEN** exactly one delta is created for the disk in this run
- **AND** the delta absorbs all changes since the last checkpoint (gap-free coverage)

### Requirement: Freeze-timestamp backup naming

Backup files SHALL be named by their own freeze point, never by snapshot data. Incremental backups SHALL use `{vm_name}.{YYYYMMDDTHHMMSS}_{disk}_{6hex}.qcow2` and FULL backups `{vm_name}.FULL.{YYYYMMDDTHHMMSS}_{disk}_{6hex}.qcow2`, where the timestamp is the backup-begin freeze point (local time, seconds resolution) and the hex suffix is `secrets.token_hex(3)`. Names SHALL remain parseable by `parse_timestamp` and `parse_disk_from_snapshot_name`.

#### Scenario: Delta named by freeze point

- **WHEN** `run_backup` creates a delta and `backup-begin` freezes at 2026-08-08T03:15:42
- **THEN** the file name matches `vm.20260808T031542_vda_{6hex}.qcow2`
- **AND** the name contains no snapshot name and no snapshot timestamp

### Requirement: Checkpoint is the sole delta baseline

Delta baselines SHALL be selected newest-wins from `virsh checkpoint-list` filtered by the `qsnap-{target_hash}-{disk}-` prefix. `IStateManager` and snapshot data SHALL NOT be consulted for baseline selection. No comparison between checkpoint timestamps and snapshot timestamps SHALL exist anywhere in the backup path.

#### Scenario: Baseline discovery uses only libvirt checkpoints

- **WHEN** `run_backup` selects a baseline for a delta
- **THEN** the selection input is exclusively the checkpoint name list for this VM+target+disk
- **AND** no snapshot timestamp participates in any comparison

### Requirement: Legacy backup files remain first-class

Pre-existing backup files named after snapshots (including FULLs whose embedded timestamp is a snapshot timestamp) SHALL remain fully usable: previous-backup resolution SHALL walk target files filtered by disk and chain via qcow2 backing headers, not via file names. Retention, chain-length counting, and restore SHALL operate over mixed generations (legacy names and freeze-ts names) without conversion.

#### Scenario: Mixed-generation chain resolves

- **WHEN** a target holds a legacy FULL named `vm.FULL.20260807T030134_vda_46abad.qcow2` and a new delta named `vm.20260808T031542_vda_a1b2c3.qcow2` backed onto it
- **THEN** chain resolution finds the FULL as the anchor via backing-chain walk
- **AND** retention and restore treat both files uniformly

### Requirement: BackupInfo model for the target world

The provider `list(target)` SHALL return `list[BackupInfo]` where `BackupInfo` is a frozen dataclass with fields `name: str`, `path: Path`, `timestamp: datetime`, `disk: str`, `is_full: bool`. `delete()` SHALL accept `BackupInfo`. `SnapshotInfo` SHALL NOT appear in the backup provider API.

#### Scenario: list returns BackupInfo

- **WHEN** `provider.list(target)` is called on a target with one FULL and one delta
- **THEN** two `BackupInfo` items are returned with correct `is_full`, `disk`, and `timestamp` values
- **AND** no `SnapshotInfo` object is returned
