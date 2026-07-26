## MODIFIED Requirements

### Requirement: NBD pull-model backup via virsh backup-begin

`BitmapBackupProvider` SHALL use the libvirt pull-model backup API. The transfer pipeline SHALL be: (1) create backup XML with NBD Unix socket, (2) create checkpoint XML naming the successor checkpoint, (3) `virsh backup-begin --domain VM backup.xml checkpoint.xml` to start the NBD export and atomically create the successor checkpoint at the export's freeze point, (4) pull data via the selected FULL transfer engine or the incremental `pread`/`pwrite` engine, (5) `flush()` the write-side (libnbd engine only), (6) cleanup socket and qemu-nbd (libnbd engine only). Checkpoints SHALL persist for subsequent incremental runs.

The incremental checkpoint SHALL be passed via an `<incremental>` element in the backup XML, NOT via a `--incremental` CLI flag. The `write_backup_xml()` function SHALL accept an optional `incremental: str | None = None` parameter. When non-None, the XML SHALL include `<incremental>{checkpoint_name}</incremental>`.

The successor checkpoint SHALL be passed as a separate checkpoint XML file given as the third positional argument to `virsh backup-begin`. Both XML temp files SHALL be removed after the run regardless of outcome.

**FULL backups SHALL use the transfer engine selected by `full_transfer_engine`** (see the `qemu-img-convert-full-backup` capability). When `full_transfer_engine == "qemu-img-convert"` (default), `_full_pull_lifecycle()` SHALL call `_qemu_img_convert_transfer()`. When `full_transfer_engine == "libnbd"`, `_full_pull_lifecycle()` SHALL call `_full_transfer_via_libnbd()` which uses `_start_write_server()` + `_transfer(zero_skip=True)`. The `pread`/`pwrite` engine is retained for incremental transfers only (design D6). For incrementals, `qemu-img create -b` (backing-chained delta), `qemu-nbd` (write-side server, uncompressed), and `qemu-img info` (verification) are used.

The `_start_write_server()` method SHALL NOT accept a `compression_type` parameter — the compress driver auto-detects the compression algorithm from the qcow2 header (set by `qemu-img create -o compression_type=...`). The `compress: bool` parameter is sufficient to select between the compress driver (`--image-opts driver=compress,...`) and plain qcow2 (`--format=qcow2`). For incremental transfers, `compress` SHALL always be `False` (design D6). For libnbd FULL transfers, `compress` SHALL be passed from the `compress` parameter.

The FULL-pull scaffolding (transfer, mv .tmp → final, finally cleanup) SHALL be shared between `transfer_missing()` full-pull and `create_full_backup()` via a private `_full_pull_lifecycle()` helper method. **`_full_pull_lifecycle()` SHALL branch on `full_transfer_engine`: when `"qemu-img-convert"`, it calls `_qemu_img_convert_transfer()`; when `"libnbd"`, it calls `_full_transfer_via_libnbd()`.** For incremental full-pulls (when `transfer_missing()` needs to create a FULL for a snapshot that has no prior backup), the same engine selection SHALL apply.

#### Scenario: First backup — full via qemu-img convert with atomic checkpoint

- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **AND** `full_transfer_engine == "qemu-img-convert"`
- **THEN** `write_backup_xml(socket_path, incremental=None)` is called
- **THEN** the backup XML does NOT contain an `<incremental>` element
- **THEN** `virsh backup-begin --domain VM backup.xml checkpoint.xml` starts a full NBD export
- **AND** the successor checkpoint is created atomically at the export's freeze point
- **THEN** `qemu-img convert` reads from `nbd:unix:<socket>` and writes to the target qcow2
- **AND** no Python `pread`/`pwrite` loop runs
- **AND** no write-side `qemu-nbd` is started

#### Scenario: First backup — full via libnbd pread/pwrite with atomic checkpoint

- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **AND** `full_transfer_engine == "libnbd"`
- **THEN** `write_backup_xml(socket_path, incremental=None)` is called
- **THEN** `virsh backup-begin --domain VM backup.xml checkpoint.xml` starts a full NBD export
- **AND** the successor checkpoint is created atomically at the export's freeze point
- **THEN** an empty qcow2 is created via `qemu-img create -f qcow2 [-o compression_type=<type>] <tmp_file> <virtual_size>`
- **AND** `_start_write_server()` starts a write-side `qemu-nbd` on a separate socket
- **AND** `_transfer(zero_skip=True)` copies all allocated blocks via `pread`/`pwrite`
- **AND** `flush()` is called on the write-side `INbdClient`
- **AND** the write-side `qemu-nbd` is terminated via its pidfile
- **AND** no `qemu-img convert` is executed

#### Scenario: Incremental backup — dirty blocks via NBD checkpoint

- **WHEN** a prior checkpoint exists and VM has written data
- **THEN** `write_backup_xml(socket_path, incremental=prior_checkpoint)` is called
- **THEN** the backup XML contains `<incremental>prior_checkpoint</incremental>`
- **THEN** `virsh backup-begin --domain VM backup.xml checkpoint.xml` starts an incremental NBD export
- **AND** a new successor checkpoint is created atomically at this export's freeze point
- **THEN** the unified engine connects with `["base:allocation", "qemu:dirty-bitmap:backup-<disk>"]` and transfers dirty∩allocated extents via `pread`/`pwrite` with `zero_skip=False`
- **AND** no `--incremental` CLI flag is passed to `virsh backup-begin`
- **AND** no `qemu-img convert` is executed for the incremental transfer
- **AND** the `full_transfer_engine` setting does NOT affect incremental transfers

#### Scenario: _start_write_server does not accept compression_type

- **WHEN** `_start_write_server(target_file, write_socket, pid_file, compress=True)` is called
- **THEN** the method signature does NOT include a `compression_type` parameter
- **AND** the compress driver auto-detects the algorithm from the qcow2 header

#### Scenario: Scaffolding dedup — shared _full_pull_lifecycle helper with engine branch

- **WHEN** `transfer_missing()` full-pull or `create_full_backup()` executes a FULL backup
- **THEN** both SHALL call the private `_full_pull_lifecycle()` helper
- **AND** the helper branches on `full_transfer_engine`: `"qemu-img-convert"` → `_qemu_img_convert_transfer()`, `"libnbd"` → `_full_transfer_via_libnbd()`
- **AND** the helper handles: transfer, mv .tmp → final, finally cleanup (socket, domjobabort, XML removal)
- **AND** the helper does NOT call `_start_write_server()` or `_transfer()` when `full_transfer_engine == "qemu-img-convert"`

#### Scenario: Socket cleanup on success

- **WHEN** the transfer completes successfully
- **THEN** the Unix socket is removed via `rm -f`
- **THEN** the forked `qemu-nbd` process is terminated via its pidfile (if started — only for `libnbd` engine)
- **THEN** the successor checkpoint is preserved as the baseline for the next incremental run

#### Scenario: Socket cleanup on failure

- **WHEN** the transfer fails (qemu-img convert error, NBD error, or stall)
- **THEN** the Unix socket is still removed via `rm -f` in a finally block
- **THEN** `BackupResult(success=False, ...)` is returned
- **AND** the prior checkpoint is preserved
- **AND** the successor checkpoint created by this failed run is deleted best-effort

### Requirement: BitmapBackupProvider.create_full_backup via configurable engine

`BitmapBackupProvider` SHALL implement `create_full_backup()` using the transfer engine selected by the `full_transfer_engine` parameter. When `full_transfer_engine == "qemu-img-convert"` (default), the method SHALL use `qemu-img convert` (NOT the unified NBD `pread`/`pwrite` engine). When `full_transfer_engine == "libnbd"`, the method SHALL use the libnbd pread/pwrite engine via `_full_transfer_via_libnbd()`. The method SHALL: (1) detect VM state via `is_vm_running()`, (2) for running VMs: start NBD export via `virsh backup-begin` with atomic checkpoint XML, then execute the selected engine, (3) for stopped VMs: direct transfer from source path, (4) atomic rename `.tmp` → final on success, (5) delete `.tmp` and cleanup socket on failure. The method SHALL NOT call `_start_write_server()` or `_transfer()` for FULL backups when `full_transfer_engine == "qemu-img-convert"`.

When `compress=True` and `compression_type="zstd"`, the `qemu-img convert` command SHALL include `-c -O qcow2 -o compression_type=zstd -m <parallel> [-W] -p`. When `compress=True` and `compression_type="zlib"`, `-o compression_type=zlib` SHALL be used. When `compress=False`, neither `-c` nor `-o compression_type=` SHALL be present. The `-m <parallel>` value SHALL come from the `convert_parallel` parameter. The `-W` flag SHALL be included when `convert_out_of_order=True` and omitted when `False`.

When `full_transfer_engine == "libnbd"` and `compress=True`, the target qcow2 SHALL be created with `qemu-img create -f qcow2 -o compression_type=<compression_type> <tmp_file> <virtual_size>` and the write-side `qemu-nbd` SHALL be started with `--image-opts driver=compress,...`. When `full_transfer_engine == "libnbd"` and `compress=False`, the target qcow2 SHALL be created with `qemu-img create -f qcow2 <tmp_file> <virtual_size>` and the write-side `qemu-nbd` SHALL be started with `--format=qcow2`.

#### Scenario: Bitmap FULL with zstd compression via qemu-img convert

- **WHEN** `create_full_backup(vm_name, snapshot, target, compress=True, compression_type="zstd", full_transfer_engine="qemu-img-convert")` is called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p <source> <target>.tmp` is executed via `run_with_stall_detection()`
- **AND** no write-side `qemu-nbd` is started
- **AND** no Python `pread`/`pwrite` loop runs

#### Scenario: Bitmap FULL without compression via qemu-img convert

- **WHEN** `create_full_backup(vm_name, snapshot, target, compress=False, full_transfer_engine="qemu-img-convert")` is called
- **THEN** `qemu-img convert -O qcow2 -m 4 -W -p <source> <target>.tmp` is executed
- **AND** no `-c` flag is present

#### Scenario: Bitmap FULL with zstd compression via libnbd

- **WHEN** `create_full_backup(vm_name, snapshot, target, compress=True, compression_type="zstd", full_transfer_engine="libnbd")` is called
- **THEN** `qemu-img create -f qcow2 -o compression_type=zstd <tmp_file> <virtual_size>` is executed
- **AND** `_start_write_server(target_file=<tmp_file>, write_socket=<socket>, pid_file=<pid>, compress=True)` is called
- **AND** `_transfer(socket_path=<source_socket>, write_socket=<write_socket>, ..., zero_skip=True, compress=True)` is called
- **AND** no `qemu-img convert` is executed

#### Scenario: Bitmap FULL without compression via libnbd

- **WHEN** `create_full_backup(vm_name, snapshot, target, compress=False, full_transfer_engine="libnbd")` is called
- **THEN** `qemu-img create -f qcow2 <tmp_file> <virtual_size>` is executed
- **AND** `_start_write_server(target_file=<tmp_file>, write_socket=<socket>, pid_file=<pid>, compress=False)` is called
- **AND** `_transfer(socket_path=<source_socket>, write_socket=<write_socket>, ..., zero_skip=True, compress=False)` is called

#### Scenario: Bitmap FULL leaves an atomic checkpoint baseline

- **WHEN** `create_full_backup()` is called for a running VM
- **THEN** `virsh backup-begin` is invoked with a checkpoint XML as the third positional argument
- **AND** on success a checkpoint named `qsnap-{target_hash}-{yyyymmddTHHMMSS}` exists

### Requirement: zero-skip for standalone FULL

The unified NBD transfer engine SHALL support a `zero_skip` parameter. When `zero_skip=True` (FULL via libnbd, no backing chain), all-zero chunks read via `pread` SHALL be skipped (no `pwrite` call) — unwritten qcow2 clusters without backing read as zeros. When `zero_skip=False` (incremental, backing-chained delta), zero-skip SHALL NOT be applied — a zero dirty-block may correspond to non-zero backing data. When `full_transfer_engine == "qemu-img-convert"`, the `zero_skip` parameter is not applicable (qemu-img convert handles the full copy internally).

#### Scenario: All-zero chunk skipped in FULL via libnbd

- **WHEN** `full_transfer_engine == "libnbd"` and `zero_skip=True` and a `pread` chunk is entirely zero bytes
- **THEN** no `pwrite` is called for that chunk
- **AND** the chunk counter for skipped bytes is incremented

#### Scenario: Non-zero chunk written in FULL via libnbd

- **WHEN** `full_transfer_engine == "libnbd"` and `zero_skip=True` and a `pread` chunk contains non-zero data
- **THEN** `pwrite` is called for that chunk

#### Scenario: Zero-skip never applied to incrementals

- **WHEN** `zero_skip=False` (incremental transfer)
- **THEN** all dirty∩allocated extents are written via `pwrite` regardless of content
- **AND** the `full_transfer_engine` setting does not affect this behavior

### Requirement: qemu-nbd compress driver for write-side compression

When `compress=True` and `full_transfer_engine == "libnbd"`, the write-side `qemu-nbd` SHALL be started with `--image-opts "driver=compress,file.driver=qcow2,file.file.driver=file,file.file.filename={target}"` instead of `--format=qcow2`. The compress driver is a QEMU block-layer driver that transparently compresses `pwrite` data into compressed qcow2 clusters. The compression algorithm is determined by the qcow2 metadata (set at creation time via `qemu-img create -o compression_type=zstd`).

When `full_transfer_engine == "qemu-img-convert"`, this requirement does not apply — compression is handled by the `-c` flag and `-o compression_type=` option of `qemu-img convert` directly.

#### Scenario: Compress driver enabled for libnbd FULL

- **WHEN** `full_transfer_engine == "libnbd"` and `compress=True` and `compression_type="zstd"`
- **THEN** the target qcow2 is created with `-o compression_type=zstd`
- **THEN** `qemu-nbd` is started with `--image-opts "driver=compress,..."`
- **THEN** `pwrite` data is transparently compressed into qcow2 clusters

#### Scenario: No compress driver when compress=False

- **WHEN** `compress=False` (regardless of `full_transfer_engine`)
- **THEN** `qemu-nbd` is started with `--format=qcow2` (no compress driver) when `full_transfer_engine == "libnbd"`
- **AND** `qemu-img convert` is executed without `-c` when `full_transfer_engine == "qemu-img-convert"`
