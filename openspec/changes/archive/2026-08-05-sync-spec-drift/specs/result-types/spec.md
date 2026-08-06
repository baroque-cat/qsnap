## MODIFIED Requirements

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
