# Result Types

## Purpose
Immutable frozen dataclasses returned by all fallible operations. Every result carries a `success` boolean and an `error` string that is non-None iff `success` is False.
## Requirements
### Requirement: SnapshotResult dataclass
The system SHALL provide an immutable `SnapshotResult` dataclass with `success: bool`, `name: str`, `path: Path`, `new_allocation: int`, `error: str | None`, and `disk: str | None = None`. The `disk` field identifies the disk target (e.g. `"vda"`) the snapshot belongs to; Core tags per-disk snapshot results with it (including simulated dry-run snapshots) so downstream consumers can attribute each snapshot to its disk. `disk` is `None` when the caller has no disk context.

#### Scenario: Successful snapshot result
- **WHEN** a `SnapshotResult` is created with `success=True`, `name="myvm.20250101T1200"`, `path=Path("/snaps/myvm.20250101T1200.qcow2")`, `new_allocation=65536`, `error=None`
- **THEN** all fields match and `result.success is True`

#### Scenario: Failed snapshot result
- **WHEN** a `SnapshotResult` is created with `success=False`, `name=""`, `path=Path()`, `new_allocation=0`, `error="virsh timed out"`
- **THEN** `result.success is False` and `result.error` contains the error message

#### Scenario: SnapshotResult carries disk
- **WHEN** a `SnapshotResult` is created for disk `vda` with `disk="vda"`
- **THEN** `result.disk` is `"vda"`; the default when omitted is `None`

### Requirement: BackupResult dataclass
The system SHALL provide an immutable `BackupResult` dataclass with `success: bool`, `snapshot_name: str`, `source_path: Path`, `target_path: Path`, `bytes_transferred: int`, `error: str | None`, `duration: float = 0.0`, `disk: str | None = None`, and `checkpoint: str | None = None`. The `disk` field identifies the disk target (e.g. `"vda"`) the transferred backup belongs to — backups of different disks within the same VM are differentiated by this field. Producers (`BitmapBackupProvider.transfer_missing`, `BitmapBackupProvider.create_full_backup`, and the Core FULL-creation path) SHALL populate `disk` from the source snapshot's disk; the default `None` exists only for construction compatibility. The `checkpoint` field carries the exact libvirt checkpoint name created during the operation — populated by `create_full_backup` on the running-VM path and `None` when no checkpoint was created (stopped-VM path) or for plain transfers.

#### Scenario: Successful backup transfer
- **WHEN** a `BackupResult` is created with `success=True`, `bytes_transferred=1048576`, `error=None`
- **THEN** `result.success is True` and `result.bytes_transferred > 0`

#### Scenario: BackupResult carries disk
- **WHEN** a `BackupResult` is created for a transfer of snapshot `myvm.20250101T120000_vda_a1b2c3` with `disk="vda"`
- **THEN** `result.disk` is `"vda"` and the dataclass is frozen

#### Scenario: BackupResult disk defaults to None
- **WHEN** a `BackupResult` is created without the `disk` argument
- **THEN** `result.disk` is `None`

#### Scenario: BackupResult carries checkpoint name
- **WHEN** a `BackupResult` is created for a running-VM FULL with `checkpoint="qsnap-ab12cd34-vda-20260807T020000-9f8e7d"`
- **THEN** `result.checkpoint` is that exact name and the dataclass is frozen

#### Scenario: BackupResult checkpoint defaults to None
- **WHEN** a `BackupResult` is created without the `checkpoint` argument
- **THEN** `result.checkpoint` is `None`

### Requirement: CommitResult dataclass
The system SHALL provide an immutable `CommitResult` dataclass representing the outcome of a commit operation (`virsh blockcommit` or `qemu-img commit`), with `success: bool`, `committed_snapshot: str`, `error: str | None`, and `outcome: str`. `outcome` SHALL be one of `"success"`, `"failure"`, or `"unknown"`, defaulting to `"failure"` so existing constructor calls remain valid. `"unknown"` denotes an indeterminate outcome (command timed out or was killed; the real state of the chain is unknown and MUST be reconciled). `success=True` SHALL imply `outcome="success"`.

#### Scenario: Successful blockcommit
- **WHEN** a `CommitResult` is created with `success=True`, `committed_snapshot="myvm.20250101T1200"`, `error=None`
- **THEN** the result indicates the named snapshot was merged into its backing file
- **AND** `outcome` is `"success"` when set explicitly by the producer

#### Scenario: Unknown outcome from timeout
- **WHEN** a `CommitResult` is created with `success=False`, `outcome="unknown"`, `error="Command timed out after 1800s"`
- **THEN** `result.outcome == "unknown"` and callers MUST NOT treat it as a definitive failure

#### Scenario: Default outcome preserves legacy constructors
- **WHEN** a `CommitResult` is created without the `outcome` argument
- **THEN** `result.outcome == "failure"` (the default) and all existing fields behave unchanged

### Requirement: RetentionResult dataclass
The system SHALL provide an immutable `RetentionResult` dataclass with `keep: list[str]` and `remove: list[str]`, representing the output of retention policy evaluation.

#### Scenario: Retention policy keeps some, removes others
- **WHEN** a `RetentionResult` is created with `keep=["snap1", "snap3"]` and `remove=["snap2"]`
- **THEN** both lists are accessible and frozen

### Requirement: ShellResult dataclass
The system SHALL provide an immutable `ShellResult` dataclass with `success: bool`, `stdout: str`, `stderr: str`, `returncode: int`, and `error: str | None`.

#### Scenario: Successful shell command
- **WHEN** a `ShellResult` is created with `success=True`, `returncode=0`, `stdout="output"`, `stderr=""`, `error=None`
- **THEN** the result indicates the command completed without errors

#### Scenario: Failed shell command
- **WHEN** a `ShellResult` is created with `success=False`, `returncode=1`, `error="command not found"`
- **THEN** `result.success is False` and `result.error` explains the failure

### Requirement: ChangeResult dataclass
The system SHALL provide an immutable `ChangeResult` dataclass with `changed: bool`, `last_allocation: int`, `current_allocation: int`, and `disk: str`. The `disk` field identifies the disk target (e.g. `"vda"`) this result applies to — change detection is per-disk.

#### Scenario: VM disk has grown
- **WHEN** a `ChangeResult` is created with `changed=True`, `last_allocation=1000000`, `current_allocation=2000000`, `disk="vda"`
- **THEN** `result.changed is True`, allocation values differ, and `result.disk` is `"vda"`

#### Scenario: VM disk has not changed
- **WHEN** a `ChangeResult` is created with `changed=False`, `last_allocation=1000000`, `current_allocation=1000000`, `disk="vdb"`
- **THEN** `result.changed is False` and `result.disk` is `"vdb"`

### Requirement: SnapshotInfo dataclass
The system SHALL provide an immutable `SnapshotInfo` dataclass with `name: str`, `path: Path`, `timestamp: datetime`, `allocation: int`, and `disk: str`. It is used by `IStateManager` to record and retrieve snapshot metadata across pipeline runs. The `disk` field identifies the disk target (e.g. `"vda"`) the snapshot belongs to — snapshots of different disks within the same VM are differentiated by this field.

#### Scenario: SnapshotInfo with disk
- **WHEN** a `SnapshotInfo` is created with `name="myvm.20250101T120000_vda_a1b2c3"`, `path=Path("/snaps/...")`, `timestamp=datetime(...)`, `allocation=65536`, `disk="vda"`
- **THEN** all fields match, `disk` is `"vda"`, and the dataclass is frozen

### Requirement: FullBackupInfo dataclass
The system SHALL provide an immutable `FullBackupInfo` dataclass with `name: str`, `path: Path`, `timestamp: datetime`, and `disk: str`. It is used by `IStateManager` to track when the last full backup was created for a given target. The `disk` field identifies the disk target (e.g. `"vda"`) this FULL anchors — each disk owns its own FULL chain.

#### Scenario: FullBackupInfo with disk
- **WHEN** a `FullBackupInfo` is created with `name="myvm.FULL.20250101T120000_a1b2c3"`, `path=Path("/backup/...")`, `timestamp=datetime(2025, 1, 1, 12, 0, 0)`, `disk="vda"`
- **THEN** all fields match, `disk` is `"vda"`, and the dataclass is frozen

### Requirement: DeferredBlockcommit dataclass
The system SHALL provide an immutable `DeferredBlockcommit` dataclass with `snapshots: list[str]`, `reason: str`, `since: datetime`, `disk: str`, and `last_warned_at: datetime | None = None`. The `disk` field identifies the disk target (e.g. `"vda"`) whose blockcommit was deferred. `last_warned_at` tracks the last time a warning was logged for this deferred operation.

#### Scenario: DeferredBlockcommit with disk
- **WHEN** a `DeferredBlockcommit` is created with `snapshots=["snap1"]`, `reason="apparmor"`, `since=datetime(...)`, `disk="vda"`
- **THEN** `deferred.disk` is `"vda"` and `deferred.last_warned_at` is `None`

### Requirement: ChainVerifyResult dataclass
The system SHALL provide an immutable `ChainVerifyResult` dataclass with `success: bool`, `error: str | None`, `broken_file: Path | None = None`, and `disk: str | None = None`. The `disk` field identifies the disk target whose chain was verified, when known.

#### Scenario: ChainVerifyResult without disk
- **WHEN** a `ChainVerifyResult` is created with `success=True`, `error=None`, no `disk`
- **THEN** `result.disk` is `None`

#### Scenario: ChainVerifyResult with disk
- **WHEN** a `ChainVerifyResult` is created with `success=False`, `error="chain broken"`, `disk="vda"`
- **THEN** `result.disk` is `"vda"`

### Requirement: RestoreResult dataclass
The system SHALL provide an immutable `RestoreResult` dataclass with `success: bool`, `snapshot_name: str`, `restored_path: Path`, `chain_files: list[Path]`, `error: str | None`, and `disk: str | None = None`. The `disk` field identifies the disk target that was restored, when known.

#### Scenario: RestoreResult with disk
- **WHEN** a `RestoreResult` is created with `success=True`, `snapshot_name="myvm.20250101T120000"`, `restored_path=Path("/restore")`, `chain_files=[...]`, `error=None`, `disk="vda"`
- **THEN** `result.disk` is `"vda"`

#### Scenario: RestoreResult without disk
- **WHEN** a `RestoreResult` is created with `success=True`, `snapshot_name="..."`, `restored_path=Path("...")`, `chain_files=[...]`, `error=None`, no `disk`
- **THEN** `result.disk` is `None`

