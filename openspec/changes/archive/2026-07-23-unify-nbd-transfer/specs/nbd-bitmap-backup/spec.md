## MODIFIED Requirements

### Requirement: NBD pull-model backup via virsh backup-begin

`BitmapBackupProvider` SHALL use the libvirt pull-model backup API. The transfer pipeline SHALL be: (1) create backup XML with NBD Unix socket, (2) create checkpoint XML naming the successor checkpoint, (3) `virsh backup-begin --domain VM backup.xml checkpoint.xml` to start the NBD export and atomically create the successor checkpoint at the export's freeze point, (4) pull data via the **unified NBD transfer engine** (see the `nbd-dirty-block-transfer` capability): connect `INbdClient` to the libvirt NBD socket, negotiate meta-contexts, query block status, and transfer extents via `pread`/`pwrite` into a qcow2 served by a forked `qemu-nbd`, (5) `flush()` the write-side, (6) cleanup socket and qemu-nbd. Checkpoints SHALL persist for subsequent incremental runs.

The incremental checkpoint SHALL be passed via an `<incremental>` element in the backup XML, NOT via a `--incremental` CLI flag. The `write_backup_xml()` function SHALL accept an optional `incremental: str | None = None` parameter. When non-None, the XML SHALL include `<incremental>{checkpoint_name}</incremental>`.

The successor checkpoint SHALL be passed as a separate checkpoint XML file given as the third positional argument to `virsh backup-begin`. Both XML temp files SHALL be removed after the run regardless of outcome.

**No `qemu-img convert` SHALL be used in the data path.** FULL and incremental transfers both use the unified `pread`/`pwrite` engine. The only qemu-utilities used are `qemu-img create` (to initialize the target qcow2), `qemu-img info` (for verification), and `qemu-nbd` (as the write-side server).

#### Scenario: First backup — full pull via NBD with atomic checkpoint

- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `write_backup_xml(socket_path, incremental=None)` is called
- **THEN** the backup XML does NOT contain an `<incremental>` element
- **THEN** `virsh backup-begin --domain VM backup.xml checkpoint.xml` starts a full NBD export
- **AND** the successor checkpoint is created atomically at the export's freeze point
- **THEN** the unified engine connects with `["base:allocation"]` only and transfers allocated extents via `pread`/`pwrite` with `zero_skip=True`
- **AND** no `qemu-img convert` is executed

#### Scenario: Incremental backup — dirty blocks via NBD checkpoint

- **WHEN** a prior checkpoint exists and VM has written data
- **THEN** `write_backup_xml(socket_path, incremental=prior_checkpoint)` is called
- **THEN** the backup XML contains `<incremental>prior_checkpoint</incremental>`
- **THEN** `virsh backup-begin --domain VM backup.xml checkpoint.xml` starts an incremental NBD export
- **AND** a new successor checkpoint is created atomically at this export's freeze point
- **THEN** the unified engine connects with `["base:allocation", "qemu:dirty-bitmap:backup-<disk>"]` and transfers dirty∩allocated extents via `pread`/`pwrite` with `zero_skip=False`
- **AND** no `--incremental` CLI flag is passed to `virsh backup-begin`

#### Scenario: Socket cleanup on success

- **WHEN** the transfer completes successfully
- **THEN** the Unix socket is removed via `rm -f`
- **THEN** the forked `qemu-nbd` process is terminated via its pidfile
- **THEN** the successor checkpoint is preserved as the baseline for the next incremental run

#### Scenario: Socket cleanup on failure

- **WHEN** the transfer fails (NBD error or stall)
- **THEN** the Unix socket is still removed via `rm -f` in a finally block
- **THEN** the forked `qemu-nbd` process is terminated
- **THEN** `BackupResult(success=False, ...)` is returned
- **AND** the prior checkpoint is preserved
- **AND** the successor checkpoint created by this failed run is deleted best-effort

### Requirement: BitmapBackupProvider.create_full_backup via unified NBD engine

`BitmapBackupProvider` SHALL implement `create_full_backup()` using the unified NBD transfer engine (not `nbd_full_export()` or `qemu-img convert`). The method SHALL: (1) generate the FULL target name `vm.FULL.YYYYMMDD.qcow2`, (2) create a standalone qcow2 via `qemu-img create -f qcow2 [-o compression_type=zstd] target.tmp`, (3) fork `qemu-nbd` on the target (with compress driver when `compress=True`), (4) `virsh backup-begin` with checkpoint XML (atomic baseline), (5) run the unified engine with `meta_contexts=["base:allocation"]`, `zero_skip=True`, (6) `flush()` the write-side, (7) terminate qemu-nbd, (8) atomic rename `.tmp` → final. The method SHALL NOT call `nbd_full_export()` or `qemu-img convert`.

When `compress=True` and `compression_type="zstd"`, the target qcow2 SHALL be created with `-o compression_type=zstd` and the write-side qemu-nbd SHALL use `--image-opts "driver=compress,file.driver=qcow2,..."`. When `compress=True` and `compression_type="zlib"`, only `-c` is used at qcow2 creation and the compress driver still wraps the write side. When `compress=False`, the write-side qemu-nbd uses `--format=qcow2` (no compress driver).

#### Scenario: Bitmap FULL with zstd compression

- **WHEN** `create_full_backup(vm_name, snapshot, target, compress=True, compression_type="zstd")` is called
- **THEN** `qemu-img create -f qcow2 -o compression_type=zstd target.tmp` creates the target
- **THEN** `qemu-nbd` is started with `--image-opts "driver=compress,file.driver=qcow2,..."`
- **THEN** the unified engine transfers allocated extents via `pread`/`pwrite` with `zero_skip=True`
- **AND** no `qemu-img convert` is executed

#### Scenario: Bitmap FULL without compression

- **WHEN** `create_full_backup(vm_name, snapshot, target, compress=False)` is called
- **THEN** `qemu-img create -f qcow2 target.tmp` creates the target
- **THEN** `qemu-nbd` is started with `--format=qcow2`
- **THEN** the unified engine transfers allocated extents via `pread`/`pwrite` with `zero_skip=True`

#### Scenario: Bitmap FULL leaves an atomic checkpoint baseline

- **WHEN** `create_full_backup()` is called for a running VM
- **THEN** `virsh backup-begin` is invoked with a checkpoint XML as the third positional argument
- **AND** on success a checkpoint named `qsnap-{target_hash}-{yyyymmddTHHMMSS}` exists

## ADDED Requirements

### Requirement: flush() before closing write-side

The unified NBD transfer engine SHALL call `dst.flush()` on the destination `INbdClient` before `dst.disconnect()` and before terminating the forked `qemu-nbd` process. This guarantees that all `pwrite` data is committed to durable storage on the underlying qcow2 file. When `can_flush()` returns `False`, `flush()` SHALL be skipped (not all NBD backends support flush).

#### Scenario: flush called after successful transfer

- **WHEN** the `pread`/`pwrite` copy loop completes
- **THEN** `dst.can_flush()` is called
- **AND** when it returns `True`, `dst.flush()` is called
- **AND** then `dst.disconnect()` is called
- **AND** then `qemu-nbd` is terminated

#### Scenario: flush skipped when unsupported

- **WHEN** `can_flush()` returns `False`
- **THEN** `flush()` is not called
- **AND** `disconnect()` proceeds normally

### Requirement: connect-retry in LibnbdClient

`LibnbdClient.connect()` SHALL retry up to 20 times with a 1-second sleep between attempts. On each failed attempt, a fresh `nbd.NBD()` handle SHALL be created (the old handle from the failed attempt is discarded). This handles the race between `virsh backup-begin` (which starts the NBD server asynchronously) and the client connect.

#### Scenario: NBD server not ready on first attempt

- **WHEN** `virsh backup-begin` has been called but the NBD server is not yet listening
- **THEN** `connect()` retries up to 20 times with 1-second sleep
- **AND** a fresh `nbd.NBD()` handle is created on each retry
- **AND** on success, the connection is established

#### Scenario: NBD server never starts

- **WHEN** the NBD server never becomes available after 20 retries
- **THEN** `connect()` returns `NbdResult(success=False, error="...")` with a timeout message

### Requirement: zero-skip for standalone FULL

The unified NBD transfer engine SHALL support a `zero_skip` parameter. When `zero_skip=True` (FULL, no backing chain), all-zero chunks read via `pread` SHALL be skipped (no `pwrite` call) — unwritten qcow2 clusters without backing read as zeros. When `zero_skip=False` (incremental, backing-chained delta), zero-skip SHALL NOT be applied — a zero dirty-block may correspond to non-zero backing data.

#### Scenario: All-zero chunk skipped in FULL

- **WHEN** `zero_skip=True` and a `pread` chunk is entirely zero bytes
- **THEN** no `pwrite` is called for that chunk
- **AND** the chunk counter for skipped bytes is incremented

#### Scenario: Non-zero chunk written in FULL

- **WHEN** `zero_skip=True` and a `pread` chunk contains non-zero data
- **THEN** `pwrite` is called for that chunk

#### Scenario: Zero-skip never applied to incrementals

- **WHEN** `zero_skip=False` (incremental transfer)
- **THEN** all dirty∩allocated extents are written via `pwrite` regardless of content

### Requirement: qemu-nbd compress driver for write-side compression

When `compress=True`, the write-side `qemu-nbd` SHALL be started with `--image-opts "driver=compress,file.driver=qcow2,file.file.driver=file,file.file.filename={target}"` instead of `--format=qcow2`. The compress driver is a QEMU block-layer driver that transparently compresses `pwrite` data into compressed qcow2 clusters. The compression algorithm is determined by the qcow2 metadata (set at creation time via `qemu-img create -o compression_type=zstd`).

#### Scenario: Compress driver enabled for compressed FULL

- **WHEN** `compress=True` and `compression_type="zstd"`
- **THEN** the target qcow2 is created with `-o compression_type=zstd`
- **THEN** `qemu-nbd` is started with `--image-opts "driver=compress,..."`
- **THEN** `pwrite` data is transparently compressed into qcow2 clusters

#### Scenario: No compress driver when compress=False

- **WHEN** `compress=False`
- **THEN** `qemu-nbd` is started with `--format=qcow2` (no compress driver)
