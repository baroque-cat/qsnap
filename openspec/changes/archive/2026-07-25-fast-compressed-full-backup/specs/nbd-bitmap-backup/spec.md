## MODIFIED Requirements

### Requirement: NBD pull-model backup via virsh backup-begin

`BitmapBackupProvider` SHALL use the libvirt pull-model backup API for **incremental** transfers. The incremental transfer pipeline SHALL be: (1) create backup XML with NBD Unix socket, (2) create checkpoint XML naming the successor checkpoint, (3) `virsh backup-begin --domain VM backup.xml checkpoint.xml` to start the NBD export and atomically create the successor checkpoint at the export's freeze point, (4) pull data via the **unified NBD transfer engine** (see the `nbd-dirty-block-transfer` capability): connect `INbdClient` to the libvirt NBD socket, negotiate meta-contexts, query block status, and transfer extents via `pread`/`pwrite` into a qcow2 served by a forked `qemu-nbd`, (5) `flush()` the write-side, (6) cleanup socket and qemu-nbd. Checkpoints SHALL persist for subsequent incremental runs.

The incremental checkpoint SHALL be passed via an `<incremental>` element in the backup XML, NOT via a `--incremental` CLI flag. The `write_backup_xml()` function SHALL accept an optional `incremental: str | None = None` parameter. When non-None, the XML SHALL include `<incremental>{checkpoint_name}</incremental>`.

The successor checkpoint SHALL be passed as a separate checkpoint XML file given as the third positional argument to `virsh backup-begin`. Both XML temp files SHALL be removed after the run regardless of outcome.

**FULL backups SHALL use `qemu-img convert` as the transfer engine** (see the `qemu-img-convert-full-backup` capability), NOT the unified `pread`/`pwrite` engine. The `pread`/`pwrite` engine is retained for incremental transfers only. The only qemu-utilities used for FULLs are `qemu-img create` (to initialize the target qcow2, when needed) and `qemu-img convert` (the data transfer). For incrementals, `qemu-img create -b` (backing-chained delta), `qemu-nbd` (write-side server, uncompressed), and `qemu-img info` (verification) are used.

The `_start_write_server()` method SHALL NOT accept a `compression_type` parameter — the compress driver auto-detects the compression algorithm from the qcow2 header (set by `qemu-img create -o compression_type=...`). The `compress: bool` parameter is sufficient to select between the compress driver (`--image-opts driver=compress,...`) and plain qcow2 (`--format=qcow2`). For incremental transfers, `compress` SHALL always be `False` (design D6).

The FULL-pull scaffolding (qemu-img create, _start_write_server, _transfer, _terminate_qemu_nbd, mv .tmp → final, finally cleanup) SHALL be shared between `transfer_missing()` full-pull and `create_full_backup()` via a private `_full_pull_lifecycle()` helper method. **For FULL backups, `_full_pull_lifecycle()` SHALL use `qemu-img convert` instead of `_start_write_server()` + `_transfer()`.** For incremental full-pulls (when `transfer_missing()` needs to create a FULL for a snapshot that has no prior backup), the same `qemu-img convert` path SHALL be used.

#### Scenario: First backup — full via qemu-img convert with atomic checkpoint

- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `write_backup_xml(socket_path, incremental=None)` is called
- **THEN** the backup XML does NOT contain an `<incremental>` element
- **THEN** `virsh backup-begin --domain VM backup.xml checkpoint.xml` starts a full NBD export
- **AND** the successor checkpoint is created atomically at the export's freeze point
- **THEN** `qemu-img convert` reads from `nbd:unix:<socket>` and writes to the target qcow2
- **AND** no Python `pread`/`pwrite` loop runs
- **AND** no write-side `qemu-nbd` is started

#### Scenario: Incremental backup — dirty blocks via NBD checkpoint

- **WHEN** a prior checkpoint exists and VM has written data
- **THEN** `write_backup_xml(socket_path, incremental=prior_checkpoint)` is called
- **THEN** the backup XML contains `<incremental>prior_checkpoint</incremental>`
- **THEN** `virsh backup-begin --domain VM backup.xml checkpoint.xml` starts an incremental NBD export
- **AND** a new successor checkpoint is created atomically at this export's freeze point
- **THEN** the unified engine connects with `["base:allocation", "qemu:dirty-bitmap:backup-<disk>"]` and transfers dirty∩allocated extents via `pread`/`pwrite` with `zero_skip=False`
- **AND** no `--incremental` CLI flag is passed to `virsh backup-begin`
- **AND** no `qemu-img convert` is executed for the incremental transfer

#### Scenario: _start_write_server does not accept compression_type

- **WHEN** `_start_write_server(target_file, write_socket, pid_file, compress=True)` is called
- **THEN** the method signature does NOT include a `compression_type` parameter
- **AND** the compress driver auto-detects the algorithm from the qcow2 header

#### Scenario: Scaffolding dedup — shared _full_pull_lifecycle helper

- **WHEN** `transfer_missing()` full-pull or `create_full_backup()` executes a FULL backup
- **THEN** both SHALL call the private `_full_pull_lifecycle()` helper
- **AND** the helper handles: qemu-img create (if needed), qemu-img convert via run_with_stall_detection, mv .tmp → final, finally cleanup
- **AND** the helper does NOT call `_start_write_server()` or `_transfer()` for FULL backups

#### Scenario: Socket cleanup on success

- **WHEN** the transfer completes successfully
- **THEN** the Unix socket is removed via `rm -f`
- **THEN** the forked `qemu-nbd` process is terminated via its pidfile (if started — not applicable for FULL via qemu-img convert)
- **THEN** the successor checkpoint is preserved as the baseline for the next incremental run

#### Scenario: Socket cleanup on failure

- **WHEN** the transfer fails (qemu-img convert error or stall)
- **THEN** the Unix socket is still removed via `rm -f` in a finally block
- **THEN** `BackupResult(success=False, ...)` is returned
- **AND** the prior checkpoint is preserved
- **AND** the successor checkpoint created by this failed run is deleted best-effort
