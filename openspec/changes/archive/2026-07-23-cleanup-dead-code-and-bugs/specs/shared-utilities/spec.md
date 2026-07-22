## REMOVED Requirements

### Requirement: Shared hash utility in qsnap.utils
**Reason**: `file_sha256()` in `qsnap/utils/hash.py` was deleted in the `2026-07-23-unify-nbd-transfer` change. The file `qsnap/utils/hash.py` no longer exists. No code references `file_sha256` or imports from `qsnap.utils.hash`.
**Migration**: No action needed. No code imports `file_sha256`.

## MODIFIED Requirements

### Requirement: Shared NBD utility functions in qsnap.utils

The system SHALL provide NBD-related utility functions in `qsnap/utils/nbd.py`. These SHALL include `is_libvirt_new_enough(shell: IShell) -> bool`, `is_vm_running(shell: IShell, vm_name: str) -> bool`, `get_first_disk_target(shell: IShell, vm_name: str) -> str`, `write_backup_xml(socket_path: str, incremental: str | None = None) -> Path`, and `write_checkpoint_xml(checkpoint_name: str) -> Path`. These functions SHALL be stateless — they accept `IShell` and config data as parameters and do NOT implement any ABC. They SHALL NOT live under `qsnap/modules/backup/`.

The `nbd_full_export()` function is REMOVED — it was deleted in the `2026-07-23-unify-nbd-transfer` change. The function is no longer referenced by any code.

The `write_backup_xml()` function SHALL accept an optional `incremental` parameter. When non-None, the generated XML SHALL include `<incremental>{incremental}</incremental>` as a child of `<domainbackup>`, before the `<server>` element. When `None`, the XML SHALL NOT contain an `<incremental>` element (full export).

No duplicate `_write_backup_xml` method SHALL exist in `qsnap/modules/backup/bitmap.py`. `BitmapBackupProvider` SHALL import and call `write_backup_xml` from `qsnap.utils.nbd`.

#### Scenario: Core imports NBD utilities from utils

- **WHEN** `Core.fork()` checks whether a VM is running
- **THEN** it SHALL import `is_vm_running` from `qsnap.utils.nbd`
- **AND** it SHALL NOT import from `qsnap.modules.backup`

#### Scenario: BitmapBackupProvider imports write_backup_xml from utils

- **WHEN** `BitmapBackupProvider.transfer_missing()` needs to write a backup XML
- **THEN** it SHALL import `write_backup_xml` from `qsnap.utils.nbd`
- **AND** it SHALL NOT define its own `_write_backup_xml` static method
- **AND** it SHALL call `write_backup_xml(socket_path, incremental=prior)` where `prior` is the checkpoint name or `None`

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
