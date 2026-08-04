## MODIFIED Requirements

### Requirement: BackupResult dataclass
The system SHALL provide an immutable `BackupResult` dataclass with `success: bool`, `snapshot_name: str`, `source_path: Path`, `target_path: Path`, `bytes_transferred: int`, `error: str | None`, `duration: float = 0.0`, and `disk: str | None = None`. The `disk` field identifies the disk target (e.g. `"vda"`) the transferred backup belongs to — backups of different disks within the same VM are differentiated by this field. Producers (`BitmapBackupProvider.transfer_missing`, `BitmapBackupProvider.create_full_backup`, and the Core FULL-creation path) SHALL populate `disk` from the source snapshot's disk; the default `None` exists only for construction compatibility.

#### Scenario: Successful backup transfer
- **WHEN** a `BackupResult` is created with `success=True`, `bytes_transferred=1048576`, `error=None`
- **THEN** `result.success is True` and `result.bytes_transferred > 0`

#### Scenario: BackupResult carries disk
- **WHEN** a `BackupResult` is created for a transfer of snapshot `myvm.20250101T120000_vda_a1b2c3` with `disk="vda"`
- **THEN** `result.disk` is `"vda"` and the dataclass is frozen

#### Scenario: BackupResult disk defaults to None
- **WHEN** a `BackupResult` is created without the `disk` argument
- **THEN** `result.disk` is `None`
