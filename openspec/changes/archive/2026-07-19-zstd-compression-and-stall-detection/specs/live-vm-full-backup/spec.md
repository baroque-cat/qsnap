## MODIFIED Requirements

### Requirement: NBD full-export helper for FULL backups

The system SHALL provide a shared NBD full-export mechanism used by both `FileCopyBackupProvider.create_full_backup()` and `BitmapBackupProvider.create_full_backup()`. The mechanism SHALL: (1) remove any stale socket at `/tmp/qsnap-backup-{pid}.sock`, (2) write a backup XML with `<domainbackup mode='pull'><server transport='unix' path='<socket>'/></domainbackup>`, (3) run `virsh backup-begin --domain <vm> <xml>` WITHOUT `--incremental` (full export, no checkpoint), (4) run `qemu-img convert -O qcow2 [-c -o compression_type=<type>] nbd:unix:<socket> <target_file>` to pull the full disk, (5) clean up the socket via `rm -f` in a `finally` block. The function signature SHALL be `nbd_full_export(shell: IShell, vm_name: str, target_file: str | Path, compress: bool = False, compression_type: str = "zstd") -> ShellResult`. When `compress=True` and `compression_type="zstd"`, the convert command SHALL include `-c -o compression_type=zstd`. When `compress=True` and `compression_type="zlib"`, the convert command SHALL include only `-c` (default zlib). The `qemu-img convert` command SHALL be executed via `IShell.run_with_stall_detection()` with `output_file` set to `target_file` and `stall_timeout` from the target config.

#### Scenario: NBD full export with zstd compression
- **WHEN** `nbd_full_export(shell, "myvm", "/target/full.qcow2", compress=True, compression_type="zstd")` is called
- **THEN** `virsh backup-begin` is called without `--incremental`
- **THEN** `qemu-img convert -O qcow2 -c -o compression_type=zstd nbd:unix:<socket> <target>` is executed
- **THEN** the resulting file has no backing file (`qemu-img info` shows `backing file: <none>`)
- **AND** the file is compressed with zstd

#### Scenario: NBD full export with zlib compression
- **WHEN** `nbd_full_export(shell, "myvm", "/target/full.qcow2", compress=True, compression_type="zlib")` is called
- **THEN** `qemu-img convert -O qcow2 -c nbd:unix:<socket> <target>` is executed (default zlib)

#### Scenario: NBD full export without compression
- **WHEN** `nbd_full_export(shell, "myvm", "/target/full.qcow2", compress=False, compression_type="zstd")` is called
- **THEN** `qemu-img convert -O qcow2 nbd:unix:<socket> <target>` is executed (no `-c` flag)
- **AND** the `compression_type` parameter is ignored

#### Scenario: NBD full export uses stall detection
- **WHEN** `nbd_full_export(shell, "myvm", "/target/full.qcow2.tmp", compress=True, compression_type="zstd")` is called
- **AND** the calling context provides `stall_timeout` from target config
- **THEN** the `qemu-img convert` command is executed via `shell.run_with_stall_detection(cmd, output_file=Path(target_file), stall_timeout=...)`
- **AND** if the output file stops growing, the convert is killed

#### Scenario: NBD socket cleaned up on success
- **WHEN** the NBD full export completes successfully
- **THEN** the Unix socket file is removed via `rm -f`

#### Scenario: NBD socket cleaned up on failure
- **WHEN** `qemu-img convert` from NBD fails
- **THEN** the Unix socket file is still removed via `rm -f` in the `finally` block
- **AND** `virsh domjobabort` is called in the `finally` block
- **AND** `ShellResult(success=False, error=<stderr>)` is returned

#### Scenario: No checkpoint created for file-copy NBD FULL
- **WHEN** `FileCopyBackupProvider` uses NBD for a FULL backup
- **THEN** no `virsh checkpoint-create-as` is called
- **AND** no `virsh checkpoint-delete` is called
- **AND** the NBD export is a one-shot frozen view with no persistent checkpoint

### Requirement: Atomic FULL file creation via NBD

When using NBD for FULL backup, the target file SHALL be created at a `.tmp` path first, then atomically renamed to the final `vm.FULL.YYYYMMDD.qcow2` name on success. This matches the existing `FileCopyBackupProvider` atomic-creation pattern. The `.tmp` file path SHALL be used as the `output_file` parameter for `run_with_stall_detection()`.

#### Scenario: NBD FULL creates tmp then renames
- **WHEN** NBD full export succeeds
- **THEN** the data is written to `<target_path>/vm.FULL.YYYYMMDD.qcow2.tmp`
- **THEN** the file is renamed to `<target_path>/vm.FULL.YYYYMMDD.qcow2`
- **AND** `BackupResult(success=True, path=<final_path>)` is returned

#### Scenario: NBD FULL failure leaves no final file
- **WHEN** NBD full export fails (including stall detection kill)
- **THEN** the `.tmp` file is removed
- **AND** no `vm.FULL.*.qcow2` file is created
- **AND** `BackupResult(success=False, error=<message>)` is returned
