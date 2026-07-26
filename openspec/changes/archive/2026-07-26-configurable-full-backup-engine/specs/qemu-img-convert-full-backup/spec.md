## MODIFIED Requirements

### Requirement: qemu-img convert as FULL backup transfer engine

`BitmapBackupProvider` SHALL use `qemu-img convert` as the FULL backup transfer engine **when `full_transfer_engine == "qemu-img-convert"`** (the default). When `full_transfer_engine == "libnbd"`, the provider SHALL use the `libnbd` pread/pwrite engine instead (see the `nbd-bitmap-backup` capability). The command SHALL be executed via `IShell.run_with_stall_detection()` with the target `.tmp` file as `output_file` and `target.backup_stall_timeout` as `stall_timeout`.

For **running VMs**, the command SHALL read from the NBD source socket started by `virsh backup-begin`:
```
qemu-img convert [-c] -O qcow2 [-o compression_type=<type>] -m <parallel> [-W] -p nbd:unix:<socket> <target>.tmp
```

For **stopped VMs**, the command SHALL read directly from the source qcow2 file:
```
qemu-img convert [-c] -O qcow2 [-o compression_type=<type>] -m <parallel> [-W] -p <source>.qcow2 <target>.tmp
```

When `compress=True`, the `-c` flag and `-o compression_type=<compression_type>` SHALL be included. When `compress=False`, neither `-c` nor `-o compression_type=` SHALL be present.

The `-m <parallel>` flag (parallel coroutines) SHALL be included with the value from the `convert_parallel` parameter (default 4). The `-W` flag (out-of-order writes) SHALL be included when `convert_out_of_order=True` (default) and SHALL be omitted when `convert_out_of_order=False`. The `-p` flag (progress bar) SHALL always be included.

The method SHALL NOT start a write-side `qemu-nbd` process. The method SHALL NOT use `_start_write_server()` or `_transfer()` for FULL backups when `full_transfer_engine == "qemu-img-convert"`. The `_start_write_server()` and `_transfer()` methods SHALL be retained for incremental backups and for the `libnbd` FULL engine path.

After `qemu-img convert` completes successfully, the `.tmp` file SHALL be atomically renamed to the final `vm.FULL.YYYYMMDD.qcow2` name. On failure, the `.tmp` file SHALL be deleted.

#### Scenario: Running VM FULL with zstd compression and default flags

- **WHEN** `create_full_backup("myvm", ..., compress=True, compression_type="zstd", full_transfer_engine="qemu-img-convert")` is called and the VM is running
- **THEN** `virsh backup-begin` starts the NBD export on a Unix socket
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p nbd:unix:<socket> <target>.tmp` is executed via `run_with_stall_detection()`
- **AND** no write-side `qemu-nbd` is started
- **AND** no Python `pread`/`pwrite` loop runs

#### Scenario: Running VM FULL without compression

- **WHEN** `create_full_backup("myvm", ..., compress=False, full_transfer_engine="qemu-img-convert")` is called and the VM is running
- **THEN** `virsh backup-begin` starts the NBD export
- **THEN** `qemu-img convert -O qcow2 -m 4 -W -p nbd:unix:<socket> <target>.tmp` is executed
- **AND** no `-c` flag is present

#### Scenario: Running VM FULL with custom parallel count

- **WHEN** `create_full_backup("myvm", ..., compress=True, compression_type="zstd", full_transfer_engine="qemu-img-convert", convert_parallel=2)` is called and the VM is running
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 2 -W -p nbd:unix:<socket> <target>.tmp` is executed
- **AND** the `-m` flag has value `2` (not the default `4`)

#### Scenario: Running VM FULL with out-of-order disabled

- **WHEN** `create_full_backup("myvm", ..., compress=False, full_transfer_engine="qemu-img-convert", convert_out_of_order=False)` is called and the VM is running
- **THEN** `qemu-img convert -O qcow2 -m 4 -p nbd:unix:<socket> <target>.tmp` is executed
- **AND** the `-W` flag is NOT present

#### Scenario: Stopped VM FULL with compression and custom flags

- **WHEN** `create_full_backup("myvm", ..., compress=True, compression_type="zstd", full_transfer_engine="qemu-img-convert", convert_parallel=8, convert_out_of_order=False)` is called and the VM is shut off
- **THEN** `virsh backup-begin` is NOT called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 8 -p <source>.qcow2 <target>.tmp` is executed
- **AND** no NBD socket is used
- **AND** the `-W` flag is NOT present

#### Scenario: Stopped VM FULL without compression

- **WHEN** `create_full_backup("myvm", ..., compress=False, full_transfer_engine="qemu-img-convert")` is called and the VM is shut off
- **THEN** `virsh backup-begin` is NOT called
- **THEN** `qemu-img convert -O qcow2 -m 4 -W -p <source>.qcow2 <target>.tmp` is executed

#### Scenario: FULL failure leaves no final file

- **WHEN** `qemu-img convert` fails (non-zero exit or stall detected)
- **THEN** the `.tmp` file is deleted
- **AND** `BackupResult(success=False, error=<message>)` is returned
- **AND** no `vm.FULL.*.qcow2` file is created

#### Scenario: FULL success atomically renames tmp to final

- **WHEN** `qemu-img convert` completes successfully
- **THEN** the `.tmp` file is renamed to `vm.FULL.YYYYMMDD.qcow2`
- **AND** `BackupResult(success=True, path=<final_path>)` is returned

### Requirement: VM state detection in create_full_backup

`BitmapBackupProvider.create_full_backup()` SHALL call `is_vm_running()` before choosing the transfer path. When the VM is running, the method SHALL use `virsh backup-begin` + `qemu-img convert nbd:unix:<socket>` (when `full_transfer_engine == "qemu-img-convert"`) or `virsh backup-begin` + libnbd pread/pwrite (when `full_transfer_engine == "libnbd"`). When the VM is stopped, the method SHALL use direct `qemu-img convert <source_path> <target>` (when `full_transfer_engine == "qemu-img-convert"`) or direct libnbd pread/pwrite from file (when `full_transfer_engine == "libnbd"`).

The source path for stopped-VM conversion SHALL be resolved via `get_first_disk_path()` — a helper that returns the file path of the VM's first disk (not the target device name).

#### Scenario: Running VM triggers NBD-based convert

- **WHEN** `create_full_backup("myvm", ...)` is called and `is_vm_running()` returns `True`
- **THEN** `virsh backup-begin` is called to start the NBD export
- **THEN** `qemu-img convert` reads from `nbd:unix:<socket>` (when engine is `qemu-img-convert`)

#### Scenario: Stopped VM triggers direct convert

- **WHEN** `create_full_backup("myvm", ...)` is called and `is_vm_running()` returns `False`
- **THEN** `virsh backup-begin` is NOT called
- **THEN** `qemu-img convert` reads directly from the source qcow2 file path (when engine is `qemu-img-convert`)
