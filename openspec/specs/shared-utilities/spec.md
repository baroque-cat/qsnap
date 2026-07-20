# Shared Utilities

## Purpose

Cross-cutting utility functions shared across domain module boundaries. These are stateless pure functions that serve multiple callers — they do not implement any ABC and live in `qsnap/utils/`.

## Requirements

### Requirement: Shared hash utility in qsnap.utils

The system SHALL provide a `file_sha256(path: Path) -> str` function in `qsnap/utils/hash.py`. This function SHALL compute the SHA-256 hash of a file's content by reading it in 8 MiB chunks (`_CHUNK_SIZE = 8 * 1024 * 1024`). The function SHALL NOT live in any domain module sub-package (`qsnap/modules/`) — it is a cross-cutting utility used by multiple domains.

#### Scenario: File hashing used by ExternalSnapshotProvider
- **WHEN** `ExternalSnapshotProvider.create()` computes a content hash for the new snapshot
- **THEN** it SHALL import `file_sha256` from `qsnap.utils.hash`
- **AND** it SHALL NOT import from `qsnap.modules.backup`

#### Scenario: File hashing used by backup verification
- **WHEN** `verify_backup()` in `qsnap/utils/verification.py` needs to hash a transferred backup
- **THEN** it SHALL import `file_sha256` from `qsnap.utils.hash`
- **AND** it SHALL NOT define its own `_file_sha256` private function

### Requirement: Shared NBD utility functions in qsnap.utils

The system SHALL provide NBD-related utility functions in `qsnap/utils/nbd.py`. These SHALL include `is_libvirt_new_enough(shell: IShell) -> bool`, `is_vm_running(shell: IShell, vm_name: str) -> bool`, `nbd_full_export(shell: IShell, vm_name: str, source_path: Path, target_path: Path, compress: bool) -> None`, `_get_first_disk_target(shell: IShell, vm_name: str) -> str`, and `write_backup_xml(socket_path: str, incremental: str | None = None) -> Path`. These functions SHALL be stateless — they accept `IShell` and config data as parameters and do NOT implement any ABC. They SHALL NOT live under `qsnap/modules/backup/`.

The `write_backup_xml()` function SHALL accept an optional `incremental` parameter. When non-None, the generated XML SHALL include `<incremental>{incremental}</incremental>` as a child of `<domainbackup>`, before the `<server>` element. When `None`, the XML SHALL NOT contain an `<incremental>` element (full export).

No duplicate `_write_backup_xml` method SHALL exist in `qsnap/modules/backup/bitmap.py`. `BitmapBackupProvider` SHALL import and call `write_backup_xml` from `qsnap.utils.nbd`.

#### Scenario: Core imports NBD utilities from utils
- **WHEN** `Core.fork()` checks whether a VM is running
- **THEN** it SHALL import `is_vm_running` from `qsnap.utils.nbd`
- **AND** it SHALL NOT import from `qsnap.modules.backup`

#### Scenario: FileCopyBackupProvider imports NBD utilities from utils
- **WHEN** `FileCopyBackupProvider.create_full_backup()` needs NBD full export
- **THEN** it SHALL import from `qsnap.utils.nbd`
- **AND** it SHALL NOT import from its own package's `nbd_helper` sub-module

#### Scenario: BitmapBackupProvider imports write_backup_xml from utils
- **WHEN** `BitmapBackupProvider.transfer_missing()` needs to write a backup XML
- **THEN** it SHALL import `write_backup_xml` from `qsnap.utils.nbd`
- **AND** it SHALL NOT define its own `_write_backup_xml` static method
- **AND** it SHALL call `write_backup_xml(socket_path, incremental=prior)` where `prior` is the checkpoint name or `None`

#### Scenario: write_backup_xml with incremental parameter
- **WHEN** `write_backup_xml(socket_path, incremental="qsnap-abc123-snap1")` is called
- **THEN** the generated XML contains `<incremental>qsnap-abc123-snap1</incremental>`
- **AND** the XML structure is `<domainbackup mode='pull'><incremental>...</incremental><server .../></domainbackup>`

#### Scenario: write_backup_xml without incremental parameter
- **WHEN** `write_backup_xml(socket_path)` is called (incremental defaults to None)
- **THEN** the generated XML does NOT contain an `<incremental>` element
- **AND** the XML structure is `<domainbackup mode='pull'><server .../></domainbackup>`

### Requirement: Shared verification functions in qsnap.utils

The system SHALL provide backup verification functions in `qsnap/utils/verification.py`. These SHALL include `verify_backup(shell: IShell, source: Path, target: Path, verify_mode: str, expected_hash: str | None = None) -> str | None` and `verify_full_backup(shell: IShell, target_path: Path, source_path: Path | None, verify_mode: str) -> str | None`. These functions SHALL be stateless, accept `IShell` as a parameter, and return `None` on success or an error string on failure. They SHALL NOT live under `qsnap/modules/backup/`.

#### Scenario: Core imports verify_full_backup from utils
- **WHEN** `Core._backup_target()` verifies a FULL after creation
- **THEN** it SHALL import `verify_full_backup` from `qsnap.utils.verification`
- **AND** it SHALL NOT import from `qsnap.modules.backup`

#### Scenario: FileCopyBackupProvider imports verify_backup from utils
- **WHEN** `FileCopyBackupProvider.transfer_missing()` verifies an incremental after transfer
- **THEN** it SHALL import `verify_backup` from `qsnap.utils.verification`

### Requirement: No domain module imports from qsnap.modules.backup.*

No file under `qsnap/modules/snapshot/`, `qsnap/modules/change/`, or `qsnap/modules/lifecycle/` SHALL import from `qsnap.modules.backup.*`. All shared functionality previously in `qsnap/modules/backup/nbd_helper.py` and `qsnap/modules/backup/verification.py` SHALL be accessed via `qsnap.utils`. Core and the factory MAY import from `qsnap.utils` directly.

#### Scenario: ExternalSnapshotProvider has no backup imports
- **WHEN** `qsnap/modules/snapshot/external.py` is inspected
- **THEN** it contains no `from qsnap.modules.backup` import statement

#### Scenario: BlockCommitManager has no backup imports
- **WHEN** `qsnap/modules/lifecycle/blockcommit_manager.py` is inspected
- **THEN** it contains no `from qsnap.modules.backup` import statement
