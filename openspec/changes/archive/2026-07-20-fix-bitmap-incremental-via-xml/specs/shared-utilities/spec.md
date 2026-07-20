## MODIFIED Requirements

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
