## Requirements

### Requirement: SnapshotResult dataclass
The system SHALL provide an immutable `SnapshotResult` dataclass with a `success` boolean, snapshot `name`, snapshot file `path`, `new_allocation` size in bytes, and an `error` string that is non-None iff `success` is False.

#### Scenario: Successful snapshot result
- **WHEN** a SnapshotResult is created with `success=True`, `name="myvm.20250101T1200"`, `path=Path("/snaps/myvm.20250101T1200.qcow2")`, `new_allocation=65536`, `error=None`
- **THEN** all fields match and `result.success is True`

#### Scenario: Failed snapshot result
- **WHEN** a SnapshotResult is created with `success=False`, `name=""`, `path=Path()`, `new_allocation=0`, `error="virsh timed out"`
- **THEN** `result.success is False` and `result.error` contains the error message

### Requirement: BackupResult dataclass
The system SHALL provide an immutable `BackupResult` dataclass with `success`, `snapshot_name`, `source_path`, `target_path`, `bytes_transferred`, and `error`.

#### Scenario: Successful backup transfer
- **WHEN** a BackupResult is created with `success=True`, `bytes_transferred=1048576`, `error=None`
- **THEN** `result.success is True` and `result.bytes_transferred > 0`

### Requirement: CommitResult dataclass
The system SHALL provide an immutable `CommitResult` dataclass representing the outcome of a `virsh blockcommit` operation, with `success`, `committed_snapshot`, and `error`.

#### Scenario: Successful blockcommit
- **WHEN** a CommitResult is created with `success=True`, `committed_snapshot="myvm.20250101T1200"`, `error=None`
- **THEN** the result indicates the named snapshot was merged into its backing file

### Requirement: RetentionResult dataclass
The system SHALL provide an immutable `RetentionResult` dataclass with `keep` and `remove` lists of snapshot/backup identifiers, representing the output of retention policy evaluation.

#### Scenario: Retention policy keeps some, removes others
- **WHEN** a RetentionResult is created with `keep=["snap1", "snap3"]` and `remove=["snap2"]`
- **THEN** both lists are accessible and frozen

### Requirement: ShellResult dataclass
The system SHALL provide an immutable `ShellResult` dataclass with `success`, `stdout`, `stderr`, `returncode`, and `error`.

#### Scenario: Successful shell command
- **WHEN** a ShellResult is created with `success=True`, `returncode=0`, `stdout="output"`, `stderr=""`, `error=None`
- **THEN** the result indicates the command completed without errors

#### Scenario: Failed shell command
- **WHEN** a ShellResult is created with `success=False`, `returncode=1`, `error="command not found"`
- **THEN** `result.success is False` and `result.error` explains the failure

### Requirement: ChangeResult dataclass
The system SHALL provide an immutable `ChangeResult` dataclass with `has_changed` boolean and `last_allocation`, `current_allocation` sizes.

#### Scenario: VM disk has grown
- **WHEN** a ChangeResult is created with `has_changed=True`, `last_allocation=1000000`, `current_allocation=2000000`
- **THEN** `result.has_changed is True` and allocation values differ
