# NBD Bitmap Backup

## Purpose

NBD pull-model backup via virsh backup-begin — replaces qemu-img convert --bitmap with libvirt backup API for dirty-block extraction over Unix socket.

## Requirements

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

### Requirement: NBD socket path uniqueness

`BitmapBackupProvider` SHALL use a process-unique Unix socket path: `/tmp/qsnap-backup-{pid}.sock`. Before starting `backup-begin`, the provider SHALL remove any stale socket at that path.

#### Scenario: Stale socket from crashed process

- **WHEN** a previous qsnap process crashed leaving `/tmp/qsnap-backup-12345.sock`
- **THEN** the new process (different PID) removes the stale socket before starting

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

### Requirement: NBD backup job termination via domjobabort

The unified NBD transfer engine SHALL call `virsh domjobabort --domain <vm>` in its cleanup path, before socket and qemu-nbd termination. On failure, a WARNING SHALL be logged but the error SHALL NOT propagate — cleanup proceeds regardless.

### Requirement: Atomic checkpoint creation on every bitmap backup-begin

Every `virsh backup-begin` issued by `BitmapBackupProvider` — both FULL exports via `create_full_backup()` and incremental exports via `transfer_missing()` — SHALL pass a checkpoint XML file as the third positional argument, creating the successor checkpoint atomically at the export's freeze point. The checkpoint name SHALL be `qsnap-{target_hash}-{yyyymmddTHHMMSS}` where the timestamp is the local creation time with seconds resolution, produced by the same clock used for snapshot naming. The provider SHALL NOT create the incremental baseline via a standalone `virsh checkpoint-create-as` call in the transfer pipeline.

#### Scenario: Checkpoint XML passed on FULL export

- **WHEN** `create_full_backup()` starts an NBD export
- **THEN** the `virsh backup-begin` command line is `virsh backup-begin --domain <vm> <backup.xml> <checkpoint.xml>`
- **AND** the checkpoint XML contains `<domaincheckpoint><name>qsnap-{target_hash}-{yyyymmddTHHMMSS}</name></domaincheckpoint>`

#### Scenario: Checkpoint XML passed on incremental export

- **WHEN** `transfer_missing()` starts an incremental NBD export against a prior checkpoint
- **THEN** the same `backup.xml checkpoint.xml` two-file invocation is used
- **AND** the successor checkpoint name differs from the prior checkpoint name (timestamp uniqueness)

#### Scenario: backup-begin failure leaves prior checkpoint intact

- **WHEN** `virsh backup-begin` fails (non-zero exit)
- **THEN** `BackupResult(success=False, ...)` is returned
- **AND** no data transfer is attempted
- **AND** the prior checkpoint remains the newest valid baseline

### Requirement: Prior checkpoint discovery is newest-wins

`BitmapBackupProvider` SHALL select the prior checkpoint for an incremental export as the **newest** `qsnap-{target_hash}-*` checkpoint, ordered by the creation timestamp embedded in the checkpoint name. Legacy names of the form `qsnap-{target_hash}-{snapshot_name}` SHALL be ordered by the timestamp embedded in the snapshot-name segment. Names whose timestamp cannot be parsed SHALL sort oldest (conservative). Discovery SHALL use `virsh checkpoint-list --name` and SHALL NOT consult `IStateManager` for checkpoint selection.

#### Scenario: Multiple checkpoints — newest selected

- **WHEN** `virsh checkpoint-list --name VM` returns `qsnap-h-20260720T010000`, `qsnap-h-20260721T010000`, and a foreign checkpoint `manual-one`
- **THEN** the provider selects `qsnap-h-20260721T010000` as prior
- **AND** `manual-one` is ignored (no `qsnap-` prefix match for this target)

#### Scenario: Legacy checkpoint name recognized

- **WHEN** the only qsnap checkpoint is `qsnap-h-3.Projects_opencode.20260721T0018_vda` (legacy format)
- **THEN** it is selected as prior using the timestamp embedded in the snapshot-name segment

#### Scenario: No checkpoints — full export

- **WHEN** no `qsnap-{target_hash}-*` checkpoint exists
- **THEN** a full NBD export is performed with an atomic successor checkpoint
- **AND** `IStateManager.get_full_backups()` is NOT consulted for this decision

### Requirement: Checkpoint rotation deletes superseded checkpoints only after successor success

After an incremental export has completed **and passed verification**, the provider SHALL delete all `qsnap-{target_hash}-*` checkpoints older than the successor checkpoint created with that export, via `virsh checkpoint-delete --metadata`. The provider SHALL NOT delete the current newest baseline before its successor checkpoint exists. Deletion failures SHALL log a WARNING and SHALL NOT fail the `BackupResult`. A crash before deletion leaves a stale older checkpoint, which the next successful run SHALL clean up via the same rule.

#### Scenario: Successful incremental rotates checkpoints

- **WHEN** an incremental export succeeds and verification passes
- **THEN** the successor checkpoint created with this export exists
- **AND** all older qsnap checkpoints for this VM+target are deleted via `virsh checkpoint-delete --metadata`
- **AND** exactly one qsnap checkpoint remains for this VM+target

#### Scenario: Export failure preserves prior, removes successor

- **WHEN** the dirty-block copy loop or verification fails during an incremental export
- **THEN** the prior checkpoint is NOT deleted
- **AND** the successor checkpoint created by the failed run is deleted best-effort
- **AND** the newest remaining qsnap checkpoint is the pre-run baseline

#### Scenario: checkpoint-delete failure is non-fatal

- **WHEN** `virsh checkpoint-delete --metadata` for a superseded checkpoint returns non-zero
- **THEN** a WARNING is logged with the checkpoint name and error
- **AND** the `BackupResult` remains `success=True`
- **AND** the stale checkpoint is retried for deletion on the next successful run

### Requirement: First incremental after FULL transfers dirty blocks since FULL start

The first `transfer_missing()` incremental after a bitmap-mode FULL SHALL export all blocks dirtied since the FULL export's freeze point, because the FULL's atomically created checkpoint is the baseline. This transfer is the true delta and SHALL NOT be skipped or replaced by a checkpoint-only no-op. The transferred byte count is bounded by guest write rate multiplied by FULL duration plus the time since the FULL — and the resulting delta file's allocated size SHALL reflect that bound (see the regression-barrier requirement).

#### Scenario: Writes during FULL export appear in the first incremental

- **WHEN** a FULL export runs while the guest writes data
- **AND** `transfer_missing()` runs afterwards in the same pipeline run
- **THEN** the first incremental export contains the blocks written during the FULL export
- **AND** the resulting delta file chains to the FULL and passes verification

#### Scenario: No writes since FULL — minimal incremental

- **WHEN** the guest wrote nothing between the FULL's freeze point and the first incremental export
- **THEN** the incremental export completes successfully with a near-empty payload (qcow2 metadata only)
- **AND** the checkpoint rotation still occurs

### Requirement: Incremental verification includes backing-file check and dirty-size regression barrier

Verification of a bitmap incremental (`target.verify != "off"`) SHALL assert: (a) `qemu-img info` reports format `qcow2`, (b) `virtual-size` matches the source disk, (c) `backing-filename` equals the resolved previous backup path, and (d) the file's `actual-size` does not exceed `dirty_bytes × 2 + 64 MiB`, where `dirty_bytes` is the sum of dirty extent lengths measured by the copy loop before transfer. Breach of any check SHALL fail the transfer with `"verification failed: ..."` and trigger the standard failure path. For `verify="hash"` or `verify="full"`, `qemu-img compare -q --force-share <snapshot> <delta>` SHALL additionally compare virtual disk content across both backing chains. A dedicated `verify_bitmap_incremental()` helper SHALL live in `qsnap/utils/verification.py`.

#### Scenario: Delta proportional to dirtied data passes

- **WHEN** the guest dirtied 100 MiB and the delta's `actual-size` is 150 MiB
- **THEN** verification passes the regression barrier (150 MiB ≤ 100×2 MiB + 64 MiB)

#### Scenario: Full-size incremental fails the barrier

- **WHEN** an "incremental" transfer produces a file whose `actual-size` approaches the full virtual disk size
- **THEN** verification fails with `"verification failed: ..."` indicating the size barrier
- **AND** the failure path runs (file removed, successor checkpoint deleted, prior preserved)

#### Scenario: Wrong backing file fails verification

- **WHEN** the delta's `backing-filename` does not name the resolved previous backup
- **THEN** verification fails before any content comparison

### Requirement: Core records incremental→FULL dependency for bitmap transfers

After a bitmap incremental transfer succeeds **and passes verification**, Core SHALL call `record_incremental_dependency()` for the incremental and its chain's FULL anchor — state recording is Core's responsibility (design D4). Retention cascade-deletion and `check` SHALL therefore see bitmap incrementals as dependents of their FULL.

#### Scenario: Bitmap incremental registered as dependent

- **WHEN** a verified bitmap incremental completes in the pipeline
- **THEN** `IStateManager.record_incremental_dependency()` is called with the incremental and FULL identifiers
- **AND** a later `check --state` reports no missing dependency for the incremental

#### Scenario: Failed transfer records nothing

- **WHEN** the bitmap incremental transfer or verification fails
- **THEN** no dependency is recorded
- **AND** state remains as before the transfer

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
