# qemu-img convert FULL Backup Transfer Engine

## Purpose

FULL backups use `qemu-img convert` (C code, parallel coroutines, ~850 MB/s zstd) as the sole FULL transfer engine. The `INbdClient` pread/pwrite loop and `_start_write_server()`/`_transfer()` methods are retained for incremental backups only, which require dirty-bitmap meta-context intersection that `qemu-img convert` cannot perform.

## Requirements

### Requirement: qemu-img convert as sole FULL backup transfer engine

`BitmapBackupProvider` SHALL use `qemu-img convert` as the sole FULL backup transfer engine. The command SHALL be executed via `IShell.run_with_stall_detection()` with the target `.tmp` file as `output_file` and `stall_timeout` as the stall-detection threshold.

For **running VMs**, the command SHALL read from the NBD source socket started by `virsh backup-begin`, with the export name set to the disk target:
```
qemu-img convert [-c] -O qcow2 [-o compression_type=<type>] -m <parallel> [-W] -p nbd:unix:<socket>:exportname=<disk> <target>.tmp
```

For **stopped VMs**, the command SHALL read directly from the source qcow2 file resolved via `get_disk_targets` filtered by the snapshot's disk:
```
qemu-img convert [-c] -O qcow2 [-o compression_type=<type>] -m <parallel> [-W] -p <source>.qcow2 <target>.tmp
```

When `compress=True`, the `-c` flag and `-o compression_type=<compression_type>` SHALL be included. When `compress=False`, neither `-c` nor `-o compression_type=` SHALL be present.

The `-m <parallel>` flag (parallel coroutines) SHALL be included with the value from the `convert_parallel` parameter (default 4). The `-W` flag (out-of-order writes) SHALL be included when `convert_out_of_order=True` (default) and SHALL be omitted when `convert_out_of_order=False`. The `-p` flag (progress bar) SHALL always be included.

The method SHALL NOT start a write-side `qemu-nbd` process. The method SHALL NOT use `_start_write_server()` or `_transfer()` for FULL backups. The `_start_write_server()` and `_transfer()` methods SHALL be retained for incremental backups only.

After `qemu-img convert` completes successfully, the `.tmp` file SHALL be atomically renamed to the final `vm.FULL.YYYYMMDDTHHMMSS_{disk}_{6hex}.qcow2` name (multi-disk refactor: disk is encoded in the FULL name). On failure, the `.tmp` file SHALL be deleted.

#### Scenario: Running VM FULL with zstd compression and default flags

- **WHEN** `create_full_backup("myvm", ..., compress=True, compression_type="zstd")` is called and the VM is running
- **THEN** `virsh backup-begin` starts the NBD export on a Unix socket, restricted to the snapshot's disk via `<disks><disk name='{disk}'/></disks>`
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p nbd:unix:<socket>:exportname=<disk> <target>.tmp` is executed via `run_with_stall_detection()`
- **AND** no write-side `qemu-nbd` is started
- **AND** no `INbdClient` pread/pwrite loop runs

#### Scenario: Running VM FULL without compression

- **WHEN** `create_full_backup("myvm", ..., compress=False)` is called and the VM is running
- **THEN** `virsh backup-begin` starts the NBD export
- **THEN** `qemu-img convert -O qcow2 -m 4 -W -p nbd:unix:<socket>:exportname=<disk> <target>.tmp` is executed
- **AND** no `-c` flag is present

#### Scenario: Running VM FULL with custom parallel count

- **WHEN** `create_full_backup("myvm", ..., compress=True, compression_type="zstd", convert_parallel=2)` is called and the VM is running
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 2 -W -p nbd:unix:<socket>:exportname=<disk> <target>.tmp` is executed
- **AND** the `-m` flag has value `2` (not the default `4`)

#### Scenario: Running VM FULL with out-of-order disabled

- **WHEN** `create_full_backup("myvm", ..., compress=False, convert_out_of_order=False)` is called and the VM is running
- **THEN** `qemu-img convert -O qcow2 -m 4 -p nbd:unix:<socket>:exportname=<disk> <target>.tmp` is executed
- **AND** the `-W` flag is NOT present

#### Scenario: Stopped VM FULL with compression and custom flags

- **WHEN** `create_full_backup("myvm", ..., compress=True, compression_type="zstd", convert_parallel=8, convert_out_of_order=False)` is called and the VM is shut off
- **THEN** `virsh backup-begin` is NOT called
- **THEN** `get_disk_targets(shell, vm_name)` resolves the source path for the snapshot's disk
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 8 -p <source>.qcow2 <target>.tmp` is executed
- **AND** no NBD socket is used
- **AND** the `-W` flag is NOT present

#### Scenario: Stopped VM FULL without compression

- **WHEN** `create_full_backup("myvm", ..., compress=False)` is called and the VM is shut off
- **THEN** `virsh backup-begin` is NOT called
- **THEN** `qemu-img convert -O qcow2 -m 4 -W -p <source>.qcow2 <target>.tmp` is executed from the resolved disk path

#### Scenario: FULL failure leaves no final file

- **WHEN** `qemu-img convert` fails (non-zero exit or stall detected)
- **THEN** the `.tmp` file is deleted
- **AND** `BackupResult(success=False, error=<message>)` is returned
- **AND** no `vm.FULL.*.qcow2` file is created

#### Scenario: FULL success atomically renames tmp to final

- **WHEN** `qemu-img convert` completes successfully
- **THEN** the `.tmp` file is renamed to `vm.FULL.YYYYMMDDTHHMMSS_{disk}_{6hex}.qcow2`
- **AND** `BackupResult(success=True, target_path=<final_path>)` is returned


### Requirement: VM state detection in create_full_backup

`BitmapBackupProvider.create_full_backup()` SHALL call `is_vm_running()` before choosing the transfer path. When the VM is running, the method SHALL use `virsh backup-begin` + `qemu-img convert nbd:unix:<socket>:exportname=<disk>`. When the VM is stopped, the method SHALL use direct `qemu-img convert <source_path> <target>`. The source path for stopped-VM conversion SHALL be resolved via `get_disk_targets(shell, vm_name)` filtered by the snapshot's disk target.

#### Scenario: Running VM triggers NBD-based convert

- **WHEN** `create_full_backup("myvm", ...)` is called and `is_vm_running()` returns `True`
- **THEN** `virsh backup-begin` is called to start the NBD export, with backup XML restricted to the snapshot's disk
- **THEN** `qemu-img convert` reads from `nbd:unix:<socket>:exportname=<disk>`

#### Scenario: Stopped VM triggers direct convert

- **WHEN** `create_full_backup("myvm", ...)` is called and `is_vm_running()` returns `False`
- **THEN** `virsh backup-begin` is NOT called
- **THEN** `get_disk_targets(shell, vm_name)` is called and filtered for the snapshot's disk target
- **AND** `qemu-img convert` reads directly from the resolved source qcow2 file path


### Requirement: get_disk_targets multi-disk helper

A helper function `get_disk_targets(shell: IShell, vm_name: str) -> list[tuple[str, str]]` SHALL be defined in `qsnap/utils/nbd.py`. It SHALL parse `virsh domblklist --domain <vm_name> --details` output and return a list of `(target, source_path)` tuples for every row whose Device is `"disk"` (excluding cdrom/floppy devices). Callers SHALL filter the result by disk target to obtain the source path for a specific disk — there is no single-disk helper.

#### Scenario: Returns all disk target/path pairs

- **WHEN** `get_disk_targets(shell, "myvm")` is called
- **AND** `virsh domblklist --domain myvm --details` returns two disk entries with targets `vda` and `vdb`
- **THEN** the function returns `[("vda", "/path/to/vda.qcow2"), ("vdb", "/path/to/vdb.qcow2")]`

#### Scenario: VM with no disks returns empty list

- **WHEN** `get_disk_targets(shell, "myvm")` is called
- **AND** `virsh domblklist` returns no disk entries
- **THEN** the function returns an empty list
