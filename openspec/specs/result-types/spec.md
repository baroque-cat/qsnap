# Result Types

## Purpose
Immutable frozen dataclasses returned by all fallible operations. Every result carries a `success` boolean and an `error` string that is non-None iff `success` is False.

## Requirements

### Requirement: SnapshotResult dataclass
The system SHALL provide an immutable `SnapshotResult` dataclass with `success: bool`, `name: str`, `path: Path`, `new_allocation: int`, and `error: str | None`.

#### Scenario: Successful snapshot result
- **WHEN** a `SnapshotResult` is created with `success=True`, `name="myvm.20250101T1200"`, `path=Path("/snaps/myvm.20250101T1200.qcow2")`, `new_allocation=65536`, `error=None`
- **THEN** all fields match and `result.success is True`

#### Scenario: Failed snapshot result
- **WHEN** a `SnapshotResult` is created with `success=False`, `name=""`, `path=Path()`, `new_allocation=0`, `error="virsh timed out"`
- **THEN** `result.success is False` and `result.error` contains the error message

### Requirement: BackupResult dataclass
The system SHALL provide an immutable `BackupResult` dataclass with `success: bool`, `snapshot_name: str`, `source_path: Path`, `target_path: Path`, `bytes_transferred: int`, `error: str | None`, and `duration: float = 0.0`.

#### Scenario: Successful backup transfer
- **WHEN** a `BackupResult` is created with `success=True`, `bytes_transferred=1048576`, `error=None`
- **THEN** `result.success is True` and `result.bytes_transferred > 0`

### Requirement: CommitResult dataclass
The system SHALL provide an immutable `CommitResult` dataclass representing the outcome of a `virsh blockcommit` operation, with `success: bool`, `committed_snapshot: str`, and `error: str | None`.

#### Scenario: Successful blockcommit
- **WHEN** a `CommitResult` is created with `success=True`, `committed_snapshot="myvm.20250101T1200"`, `error=None`
- **THEN** the result indicates the named snapshot was merged into its backing file

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
