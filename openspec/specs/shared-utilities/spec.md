# Shared Utilities

## Purpose

Cross-cutting utility functions shared across domain module boundaries. These are stateless pure functions that serve multiple callers — they do not implement any ABC and live in `qsnap/utils/`.

## Requirements

### Requirement: Shared NBD utility functions in qsnap.utils

The system SHALL provide NBD-related utility functions in `qsnap/utils/nbd.py`. These SHALL include `is_libvirt_new_enough(shell: IShell) -> bool`, `is_vm_running(shell: IShell, vm_name: str) -> bool`, `get_disk_targets(shell: IShell, vm_name: str) -> list[tuple[str, str]]` (all disk `(target, source_path)` pairs), `write_backup_xml(socket_path: str, incremental: str | None = None, disk: str | None = None) -> Path`, and `write_checkpoint_xml(checkpoint_name: str) -> Path`. These functions SHALL be stateless — they accept `IShell` and config data as parameters and do NOT implement any ABC. They SHALL NOT live under `qsnap/modules/backup/`.

The single-disk helpers `get_first_disk_target()` and `get_first_disk_path()` are REMOVED — replaced by the multi-disk `get_disk_targets()`. The `nbd_full_export()` function is REMOVED — it was deleted in the `2026-07-23-unify-nbd-transfer` change.

The `write_backup_xml()` function SHALL accept optional `incremental` and `disk` parameters. When `incremental` is non-None, the generated XML SHALL include `<incremental>{incremental}</incremental>` as a child of `<domainbackup>`, before the `<server>` element. When `disk` is non-None, the XML SHALL include a `<disks><disk name='{disk}'/></disks>` element restricting the export to that disk. When `incremental` is `None`, the XML SHALL NOT contain an `<incremental>` element (full export).

No duplicate `_write_backup_xml` method SHALL exist in `qsnap/modules/backup/bitmap.py`. `BitmapBackupProvider` SHALL import and call `write_backup_xml` from `qsnap.utils.nbd`.

#### Scenario: Core imports NBD utilities from utils

- **WHEN** `Core.fork()` checks whether a VM is running
- **THEN** it SHALL import `is_vm_running` from `qsnap.utils.nbd`
- **AND** it SHALL NOT import from `qsnap.modules.backup`

#### Scenario: BitmapBackupProvider imports write_backup_xml from utils
- **WHEN** `BitmapBackupProvider.transfer_missing()` needs to write a backup XML
- **THEN** it SHALL import `write_backup_xml` from `qsnap.utils.nbd`
- **AND** it SHALL NOT define its own `_write_backup_xml` static method
- **AND** it SHALL call `write_backup_xml(socket_path, incremental=prior, disk=snapshot.disk)`

#### Scenario: write_backup_xml with incremental parameter
- **WHEN** `write_backup_xml(socket_path, incremental="qsnap-abc123-snap1")` is called
- **THEN** the generated XML contains `<incremental>qsnap-abc123-snap1</incremental>`

#### Scenario: write_backup_xml with disk parameter
- **WHEN** `write_backup_xml(socket_path, disk="vdb")` is called
- **THEN** the generated XML contains `<disks><disk name='vdb'/></disks>`

#### Scenario: write_backup_xml without incremental parameter
- **WHEN** `write_backup_xml(socket_path)` is called (incremental defaults to None)
- **THEN** the generated XML does NOT contain an `<incremental>` element

### Requirement: Shared verification functions in qsnap.utils

The system SHALL provide backup verification functions in `qsnap/utils/verification.py`. These SHALL include `verify_full_backup(shell: IShell, target_path: Path, verify_mode: str, source_path: Path | None = None, expected_virtual_size: int | None = None) -> str | None` and `verify_bitmap_incremental(shell: IShell, delta_path: Path, source_path: Path, verify_mode: str, expected_backing: str, dirty_bytes: int) -> str | None`. These functions SHALL be stateless, accept `IShell` as a parameter, and return `None` on success or an error string on failure. They SHALL NOT live under `qsnap/modules/backup/`.

The `verify_backup()` function is REMOVED — it was a file-copy-oriented helper deleted in the `2026-07-22-remove-rsync-filecopy` change.

The `is_retryable()` function in `qsnap/utils/retry.py` SHALL NOT match the pattern `"verification failed: hash mismatch"` — the hash verification code path was deleted and this error string can never be produced.

#### Scenario: Core imports verify_full_backup from utils
- **WHEN** `Core._backup_target()` verifies a FULL after creation
- **THEN** it SHALL import `verify_full_backup` from `qsnap.utils.verification`
- **AND** it SHALL NOT import from `qsnap.modules.backup`

#### Scenario: is_retryable does not match hash mismatch

- **WHEN** `is_retryable("verification failed: hash mismatch")` is called
- **THEN** it SHALL return `False` (the pattern is dead code)

### Requirement: No domain module imports from qsnap.modules.backup.*

No file under `qsnap/modules/snapshot/`, `qsnap/modules/change/`, or `qsnap/modules/lifecycle/` SHALL import from `qsnap.modules.backup.*`. All shared functionality previously in `qsnap/modules/backup/nbd_helper.py` and `qsnap/modules/backup/verification.py` SHALL be accessed via `qsnap.utils`. Core and the factory MAY import from `qsnap.utils` directly.

#### Scenario: ExternalSnapshotProvider has no backup imports
- **WHEN** `qsnap/modules/snapshot/external.py` is inspected
- **THEN** it contains no `from qsnap.modules.backup` import statement

#### Scenario: BlockCommitManager has no backup imports
- **WHEN** `qsnap/modules/lifecycle/blockcommit_manager.py` is inspected
- **THEN** it contains no `from qsnap.modules.backup` import statement
