# NBD Bitmap Backup

## Purpose

NBD pull-model backup via virsh backup-begin — uses the libvirt backup API for dirty-block extraction over Unix socket with per-disk checkpoints, sockets, and backup chains.

## Requirements

### Requirement: NBD pull-model backup via virsh backup-begin

`BitmapBackupProvider` SHALL use the libvirt pull-model backup API. The transfer pipeline SHALL be: (1) create backup XML with NBD Unix socket at `/tmp/qsnap-backup-{pid}-{disk}.sock` and a checkpoint XML naming the successor checkpoint, (2) `virsh backup-begin --domain VM backup.xml checkpoint.xml` to start the NBD export and atomically create the successor checkpoint at the export's freeze point, (3) for FULL exports: `qemu-img convert` reads from `nbd:unix:<socket>:exportname=<disk>`; for incremental exports: `INbdClient` pread/pwrite loop copies dirty∩allocated extents into a backing-chained qcow2 delta, (4) cleanup socket. Checkpoints SHALL persist for subsequent incremental runs.

The incremental baseline checkpoint SHALL be passed via an `<incremental>` element in the backup XML, NOT via a `--incremental` CLI flag. The `write_backup_xml()` function SHALL accept optional `incremental: str | None = None` and `disk: str | None = None` parameters. When `incremental` is non-None, the XML SHALL include `<incremental>{checkpoint_name}</incremental>`. When `disk` is non-None (multi-disk refactor), the XML SHALL include `<disks><disk name='{disk}'/></disks>` to restrict the export to that single disk.

The successor checkpoint SHALL be passed as a separate checkpoint XML file given as the third positional argument to `virsh backup-begin`. Both XML temp files SHALL be removed after the run regardless of outcome.

FULL backups SHALL always use `qemu-img convert` via `_qemu_img_convert_transfer()`. The `INbdClient` pread/pwrite engine is retained for incremental transfers only. Incrementals are always uncompressed — they use `qemu-img create -b` (backing-chained delta), `qemu-nbd` (write-side server, uncompressed), and `qemu-img info` (verification).

The `_start_write_server()` method SHALL accept only a `compress: bool` parameter (not `compression_type`). For incremental transfers, `compress` SHALL always be `False`. The compress driver auto-detects the compression algorithm from the qcow2 header.

The FULL-pull scaffolding (transfer, mv .tmp → final, finally cleanup) SHALL be shared between `transfer_missing()` full-pull and `create_full_backup()` via a private `_full_pull_lifecycle()` helper method. `_full_pull_lifecycle()` SHALL call `_qemu_img_convert_transfer()` unconditionally.

#### Scenario: First backup — full via qemu-img convert with atomic checkpoint

- **WHEN** no prior qsnap checkpoint exists for this VM+target+disk combination
- **THEN** `write_backup_xml(socket_path, incremental=None, disk=disk_target)` is called
- **THEN** the backup XML does NOT contain an `<incremental>` element
- **AND** the backup XML contains `<disks><disk name='{disk}'/></disks>`
- **THEN** `virsh backup-begin --domain VM backup.xml checkpoint.xml` starts a full NBD export restricted to that disk
- **AND** the successor checkpoint is created atomically at the export's freeze point
- **THEN** `qemu-img convert` reads from `nbd:unix:<socket>:exportname=<disk>` and writes to the target qcow2
- **AND** no `INbdClient` pread/pwrite loop runs
- **AND** no write-side `qemu-nbd` is started for FULL

#### Scenario: Incremental backup — dirty blocks via NBD checkpoint

- **WHEN** a prior checkpoint exists and VM has written data
- **THEN** `write_backup_xml(socket_path, incremental=prior_checkpoint, disk=disk_target)` is called
- **THEN** the backup XML contains `<incremental>prior_checkpoint</incremental>`
- **AND** the backup XML contains `<disks><disk name='{disk}'/></disks>`
- **THEN** `virsh backup-begin --domain VM backup.xml checkpoint.xml` starts an incremental NBD export restricted to that disk
- **AND** a new successor checkpoint is created atomically at this export's freeze point
- **THEN** the `INbdClient` engine connects with `["base:allocation", "qemu:dirty-bitmap:backup-<disk>"]` and transfers dirty∩allocated extents with `zero_skip=False`
- **AND** no `--incremental` CLI flag is passed to `virsh backup-begin`
- **AND** no `qemu-img convert` is executed for the incremental transfer

#### Scenario: _start_write_server does not accept compression_type

- **WHEN** `_start_write_server(target_file, write_socket, pid_file, compress=True)` is called
- **THEN** the method signature does NOT include a `compression_type` parameter
- **AND** the compress driver auto-detects the algorithm from the qcow2 header

#### Scenario: Scaffolding dedup — shared _full_pull_lifecycle helper

- **WHEN** `transfer_missing()` full-pull or `create_full_backup()` executes a FULL backup
- **THEN** both SHALL call the private `_full_pull_lifecycle()` helper
- **AND** the helper calls `_qemu_img_convert_transfer()` unconditionally
- **AND** the helper handles: transfer, mv .tmp → final, finally cleanup (socket, domjobabort, XML removal)
- **AND** the helper does NOT call `_start_write_server()` or `_transfer()`

#### Scenario: Socket cleanup on success

- **WHEN** the transfer completes successfully
- **THEN** the Unix socket is removed via `rm -f`
- **THEN** the successor checkpoint is preserved as the baseline for the next incremental run

#### Scenario: Socket cleanup on failure

- **WHEN** the transfer fails (`qemu-img convert` error, NBD error, or stall)
- **THEN** the Unix socket is still removed via `rm -f` in a finally block
- **THEN** `BackupResult(success=False, ...)` is returned
- **AND** the prior checkpoint is preserved
- **AND** the successor checkpoint created by this failed run is deleted best-effort


### Requirement: Per-disk NBD socket path uniqueness

`BitmapBackupProvider` SHALL use process-unique and disk-unique Unix socket paths: `/tmp/qsnap-backup-{pid}-{disk}.sock` (source/read socket from libvirt) and `/tmp/qsnap-write-{pid}-{disk}.sock` (write socket for the forked `qemu-nbd`). Before starting `backup-begin`, the provider SHALL remove any stale socket at the source path.

#### Scenario: Stale socket from crashed process

- **WHEN** a previous qsnap process crashed leaving `/tmp/qsnap-backup-12345-vda.sock`
- **THEN** the new process (different PID) removes the stale socket before starting

#### Scenario: Different disks use different sockets

- **WHEN** a VM has disks `vda` and `vdb`
- **THEN** the source socket for `vda` is `/tmp/qsnap-backup-{pid}-vda.sock`
- **AND** the source socket for `vdb` is `/tmp/qsnap-backup-{pid}-vdb.sock`


### Requirement: BitmapBackupProvider.create_full_backup via qemu-img convert

`BitmapBackupProvider` SHALL implement `create_full_backup()` using `qemu-img convert` as the sole FULL backup transfer engine. The method SHALL: (1) detect VM state via `is_vm_running()`, (2) for running VMs: start NBD export via `virsh backup-begin` with atomic checkpoint XML, then execute `qemu-img convert nbd:unix:<socket>:exportname=<disk>`, (3) for stopped VMs: direct `qemu-img convert` from the source path resolved via `get_disk_targets` filtered by the snapshot's disk, (4) atomic rename `.tmp` → final on success, (5) delete `.tmp` and cleanup socket on failure.

When `compress=True` and `compression_type="zstd"`, the `qemu-img convert` command SHALL include `-c -O qcow2 -o compression_type=zstd -m <parallel> [-W] -p`. When `compress=True` and `compression_type="zlib"`, `-o compression_type=zlib` SHALL be used. When `compress=False`, neither `-c` nor `-o compression_type=` SHALL be present. The `-m <parallel>` value SHALL come from the `convert_parallel` parameter. The `-W` flag SHALL be included when `convert_out_of_order=True` and omitted when `False`.

FULL backup naming includes the disk target: `{vm_name}.FULL.{YYYYMMDDTHHMMSS}_{disk}_{6hex}.qcow2`.

#### Scenario: Bitmap FULL with zstd compression via qemu-img convert

- **WHEN** `create_full_backup(vm_name, snapshot, target, compress=True, compression_type="zstd")` is called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p <source> <target>.tmp` is executed via `run_with_stall_detection()`
- **AND** no write-side `qemu-nbd` is started
- **AND** no `INbdClient` pread/pwrite loop runs

#### Scenario: Bitmap FULL without compression via qemu-img convert

- **WHEN** `create_full_backup(vm_name, snapshot, target, compress=False)` is called
- **THEN** `qemu-img convert -O qcow2 -m 4 -W -p <source> <target>.tmp` is executed
- **AND** no `-c` flag is present

#### Scenario: Bitmap FULL leaves an atomic checkpoint baseline (running VM)

- **WHEN** `create_full_backup()` is called for a running VM
- **THEN** `write_backup_xml` includes `<disks><disk name='{disk}'/></disks>` restricting the export to that disk
- **THEN** `virsh backup-begin` is invoked with a checkpoint XML as the third positional argument
- **AND** on success a checkpoint named `qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6hex}` exists

#### Scenario: Stopped VM FULL reads from resolved disk path

- **WHEN** `create_full_backup()` is called for a stopped VM with snapshot disk `vda`
- **THEN** `get_disk_targets(shell, vm_name)` is called and filtered for target `vda`
- **AND** `qemu-img convert <resolved_source_path> <target>.tmp` is executed
- **AND** no `virsh backup-begin` is called


### Requirement: NBD backup job termination via domjobabort

The NBD transfer engine SHALL call `virsh domjobabort --domain <vm>` in its cleanup path, before socket and qemu-nbd termination. On failure, a WARNING SHALL be logged but the error SHALL NOT propagate — cleanup proceeds regardless.

#### Scenario: domjobabort called on cleanup

- **WHEN** an NBD backup transfer completes or fails
- **THEN** `virsh domjobabort --domain <vm>` is invoked before the socket and qemu-nbd are torn down

#### Scenario: domjobabort failure is non-fatal

- **WHEN** `virsh domjobabort` returns a non-zero exit code
- **THEN** a WARNING is logged and cleanup continues without raising

### Requirement: Atomic checkpoint creation on every bitmap backup-begin

Every `virsh backup-begin` issued by `BitmapBackupProvider` — both FULL exports via `create_full_backup()` and incremental exports via `transfer_missing()` — SHALL pass a checkpoint XML file as the third positional argument, creating the successor checkpoint atomically at the export's freeze point. The checkpoint name SHALL be `qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6hex}` where the timestamp is local time with seconds resolution and the hex suffix is generated via `secrets.token_hex(3)` for collision resistance. The disk target scopes the checkpoint so each disk owns its own dirty-bitmap lineage. The provider SHALL NOT create the incremental baseline via a standalone `virsh checkpoint-create-as` call.

#### Scenario: Checkpoint XML passed on FULL export

- **WHEN** `create_full_backup()` starts an NBD export
- **THEN** the `virsh backup-begin` command line is `virsh backup-begin --domain <vm> <backup.xml> <checkpoint.xml>`
- **AND** the checkpoint XML contains `<domaincheckpoint><name>qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6hex}</name></domaincheckpoint>`

#### Scenario: Checkpoint XML passed on incremental export

- **WHEN** `transfer_missing()` starts an incremental NBD export against a prior checkpoint
- **THEN** the same `backup.xml checkpoint.xml` two-file invocation is used
- **AND** the successor checkpoint name differs from the prior checkpoint name (timestamp and hex-suffix uniqueness)

#### Scenario: backup-begin failure leaves prior checkpoint intact

- **WHEN** `virsh backup-begin` fails (non-zero exit)
- **THEN** `BackupResult(success=False, ...)` is returned
- **AND** no data transfer is attempted
- **AND** the prior checkpoint remains the newest valid baseline


### Requirement: Prior checkpoint discovery is newest-wins per disk

`BitmapBackupProvider` SHALL select the prior checkpoint for an incremental export as the newest `qsnap-{target_hash}-{disk}-*` checkpoint for that specific disk, ordered by the creation timestamp embedded in the checkpoint name. Names whose timestamp cannot be parsed SHALL sort oldest (conservative). Discovery SHALL use `virsh checkpoint-list --name` and SHALL NOT consult `IStateManager` for checkpoint selection. After selection, the provider SHALL assess the selected checkpoint's bitmap health (capability `checkpoint-bitmap-health-probe`). HEALTHY SHALL proceed to the delta export. DEAD SHALL route into bitmap-loss recovery (capability `bitmap-loss-recovery`) instead of attempting the delta. UNKNOWN SHALL attempt the delta as before, relying on the reactive backstop for `checkpoint inconsistent` failures.

`_list_checkpoints_for_target(vm_name, target_hash, disk)` SHALL filter checkpoints by the prefix `qsnap-{target_hash}-{disk}-`.

#### Scenario: Multiple checkpoints — newest selected

- **WHEN** `virsh checkpoint-list --name VM` returns `qsnap-abc-vda-20260720T010000-aaaaaa`, `qsnap-abc-vda-20260721T010000-bbbbbb`, and a foreign checkpoint `manual-one`
- **THEN** the provider selects `qsnap-abc-vda-20260721T010000-bbbbbb` as prior for disk `vda`
- **AND** `manual-one` is ignored (no `qsnap-` prefix match)

#### Scenario: Different disks have separate checkpoint lineages

- **WHEN** checkpoints exist for both `qsnap-abc-vda-*` and `qsnap-abc-vdb-*`
- **THEN** `_list_checkpoints_for_target(vm, "abc", "vda")` returns only the `vda` checkpoints
- **AND** `_list_checkpoints_for_target(vm, "abc", "vdb")` returns only the `vdb` checkpoints

#### Scenario: No checkpoints for a disk — full export

- **WHEN** no `qsnap-{target_hash}-{disk}-*` checkpoint exists for this disk
- **AND** checkpoints for other disks on the same target exist
- **THEN** a full NBD export is performed for this disk with an atomic successor checkpoint
- **AND** `IStateManager` is NOT consulted for this decision

#### Scenario: Healthy checkpoint proceeds to delta

- **WHEN** the newest checkpoint's bitmap probe returns HEALTHY
- **THEN** the delta export proceeds exactly as before

#### Scenario: Dead checkpoint routes to recovery

- **WHEN** the newest checkpoint's bitmap probe returns DEAD
- **THEN** no delta `backup-begin` is attempted against that checkpoint
- **AND** the bitmap-loss recovery path decides between recovered delta and FULL

#### Scenario: Unknown probe result attempts delta

- **WHEN** the bitmap probe returns UNKNOWN
- **THEN** the delta export is attempted as before
- **AND** a subsequent `checkpoint inconsistent` failure is handled by the reactive backstop


### Requirement: Checkpoint rotation deletes superseded checkpoints per disk after successor success

After an incremental export has completed and passed verification, the provider SHALL delete all `qsnap-{target_hash}-{disk}-*` checkpoints older than the successor checkpoint created with that export, via `_delete_superseded_checkpoints(vm_name, target_hash, disk, successor)`. The provider SHALL NOT delete the current newest baseline before its successor checkpoint exists. Deletion failures SHALL log a WARNING and SHALL NOT fail the `BackupResult`. A crash before deletion leaves a stale older checkpoint, which the next successful run SHALL clean up via the same rule.

#### Scenario: Successful incremental rotates checkpoints

- **WHEN** an incremental export succeeds and verification passes
- **THEN** the successor checkpoint created with this export exists
- **AND** all older qsnap checkpoints for this VM+target+disk are deleted
- **AND** exactly one qsnap checkpoint remains for this VM+target+disk

#### Scenario: Export failure preserves prior, removes successor

- **WHEN** the dirty-block copy loop or verification fails during an incremental export
- **THEN** the prior checkpoint is NOT deleted
- **AND** the successor checkpoint created by the failed run is deleted best-effort
- **AND** the newest remaining qsnap checkpoint is the pre-run baseline

#### Scenario: checkpoint-delete failure is non-fatal

- **WHEN** `virsh checkpoint-delete` for a superseded checkpoint returns non-zero
- **THEN** a WARNING is logged with the checkpoint name and error
- **AND** the `BackupResult` remains `success=True`
- **AND** the stale checkpoint is retried for deletion on the next successful run


### Requirement: First incremental after FULL transfers dirty blocks since FULL start

The first `transfer_missing()` incremental after a bitmap-mode FULL SHALL export all blocks dirtied since the FULL export's freeze point, because the FULL's atomically created checkpoint is the baseline. This transfer is the true delta. The transferred byte count is bounded by guest write rate multiplied by FULL duration plus the time since the FULL — and the resulting delta file's allocated size SHALL reflect that bound.

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

Verification of a bitmap incremental (`target.verify != "off"`) SHALL assert: (a) `qemu-img info` reports format `qcow2`, (b) `virtual-size` matches the source disk, (c) `backing-filename` equals the resolved previous backup path, and (d) the file's `actual-size` does not exceed `dirty_bytes × 2 + 64 MiB`, where `dirty_bytes` is the sum of dirty extent lengths measured by the copy loop before transfer. Breach of any check SHALL fail the transfer with `"verification failed: ..."` and trigger the standard failure path. For `verify="compare"`, `qemu-img compare -q --force-share <snapshot> <delta>` SHALL additionally compare virtual disk content across both backing chains. A dedicated `verify_bitmap_incremental()` helper SHALL live in `qsnap/utils/verification.py`. The legacy values `"hash"` and `"full"` SHALL be treated as `"compare"` with a deprecation WARNING.

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

After a bitmap incremental transfer succeeds and passes verification, Core SHALL call `record_incremental_dependency()` for the incremental and its chain's FULL anchor — state recording is Core's responsibility. The method SHALL NOT accept a disk parameter — the disk is encoded in the FULL name. Retention cascade-deletion and `check` SHALL therefore see bitmap incrementals as dependents of their FULL.

#### Scenario: Bitmap incremental registered as dependent

- **WHEN** a verified bitmap incremental completes in the pipeline
- **THEN** `IStateManager.record_incremental_dependency(target_path, incremental_name, full_name)` is called
- **AND** a later `check --state` reports no missing dependency for the incremental

#### Scenario: Failed transfer records nothing

- **WHEN** the bitmap incremental transfer or verification fails
- **THEN** no dependency is recorded
- **AND** state remains as before the transfer


### Requirement: flush() before closing write-side

The unified NBD transfer engine SHALL call `dst.flush()` on the destination `INbdClient` before `dst.disconnect()` and before terminating the forked `qemu-nbd` process. This guarantees that all `pwrite` data is committed to durable storage on the underlying qcow2 file. When `can_flush()` returns `False`, `flush()` SHALL be skipped.

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

`LibnbdClient.connect()` SHALL retry up to 20 times with a 1-second sleep between attempts. On each failed attempt, a fresh `nbd.NBD()` handle SHALL be created. This handles the race between `virsh backup-begin` and the client connect. Before attempting the import, `connect()` SHALL call `_ensure_system_site_packages()` to make system-installed libnbd bindings discoverable when running inside a venv. After importing `nbd`, `connect()` SHALL verify that the module has `Error` and `NBD` attributes — if not, it SHALL return `NbdResult(success=False, ...)` with an actionable error message indicating the wrong package is installed.

#### Scenario: NBD server not ready on first attempt

- **WHEN** `virsh backup-begin` has been called but the NBD server is not yet listening
- **THEN** `connect()` retries up to 20 times with 1-second sleep
- **AND** a fresh `nbd.NBD()` handle is created on each retry
- **AND** on success, the connection is established

#### Scenario: NBD server never starts

- **WHEN** the NBD server never becomes available after 20 retries
- **THEN** `connect()` returns `NbdResult(success=False, error="...")` with a timeout message

#### Scenario: PyPI nbd imposter installed

- **WHEN** the PyPI `nbd` package (Jupyter notebook diffing tool) is installed instead of system `python3-libnbd`
- **AND** `connect()` successfully imports `nbd` but `hasattr(nbd, "NBD")` returns `False`
- **THEN** `connect()` returns `NbdResult(success=False, ...)` with an error message instructing to uninstall the PyPI package and install the system package

#### Scenario: System site-packages discovered in venv

- **WHEN** `connect()` is called while running inside a venv
- **THEN** `_ensure_system_site_packages()` appends system site-packages paths to `sys.path` before the import attempt
- **AND** the system `libnbd` bindings become importable


### Requirement: zero-skip for FULL backups only via qemu-img convert

FULL backups use `qemu-img convert` which handles the full copy internally. The `zero_skip` parameter on `_transfer()` is applicable only to the `INbdClient` incremental pread/pwrite engine. Incrementals SHALL always use `zero_skip=False`.

#### Scenario: Zero-skip never applied to incrementals

- **WHEN** `zero_skip=False` (incremental transfer)
- **THEN** all dirty∩allocated extents are written via `pwrite` regardless of content


### Requirement: qemu-nbd compress driver for write-side compression

The `qemu-nbd` compress driver is used only for incremental transfers (via `_start_write_server()` with `compress=True`). Compression for FULL backups is handled by the `-c` flag and `-o compression_type=` option of `qemu-img convert` directly. For bitmap incrementals, `compress` SHALL always be `False` — incrementals are never compressed.

#### Scenario: Incremental with compress=False

- **WHEN** `_start_write_server()` is called for an incremental transfer
- **THEN** `compress=False` is passed
- **AND** `qemu-nbd` runs with `--format=qcow2` (not the compress driver)


### Requirement: libnbd module attribute verification

`is_libnbd_available()` SHALL verify that the `nbd` module has the required libnbd attributes (`Error` and `NBD`) after import, not just check module existence via `find_spec`. This prevents false positives from the unrelated PyPI `nbd` package. The function SHALL call `_ensure_system_site_packages()` before the import attempt.

#### Scenario: System libnbd installed — returns True

- **WHEN** system `python3-libnbd` is installed and `is_libnbd_available()` is called
- **THEN** the function returns `True`

#### Scenario: PyPI nbd imposter — returns False

- **WHEN** the PyPI `nbd` package is installed (no `nbd.Error` or `nbd.NBD` attributes)
- **AND** `is_libnbd_available()` is called
- **THEN** the function returns `False`

#### Scenario: No nbd module at all — returns False

- **WHEN** neither system libnbd nor PyPI nbd is installed
- **THEN** the function returns `False` without attempting an import

#### Scenario: Venv discovers system libnbd

- **WHEN** qsnap runs in a venv without `--system-site-packages`
- **AND** system `libnbd` is installed at `/usr/lib/python3.x/site-packages/`
- **AND** `is_libnbd_available()` is called
- **THEN** `_ensure_system_site_packages()` appends the system path to `sys.path`
- **AND** the function returns `True`


### Requirement: MISSING_LIBNBD_ERROR multi-distro message

The `MISSING_LIBNBD_ERROR` constant SHALL include install instructions for multiple distributions (Arch, Debian, Fedora) and SHALL explicitly warn against `pip install nbd` (the unrelated PyPI package).

#### Scenario: Error message includes Arch instructions

- **WHEN** `MISSING_LIBNBD_ERROR` is displayed
- **THEN** the message includes `pacman -S libnbd` for Arch Linux

#### Scenario: Error message warns about PyPI imposter

- **WHEN** `MISSING_LIBNBD_ERROR` is displayed
- **THEN** the message includes a warning that `pip install nbd` installs an unrelated package


### Requirement: Full checkpoint deletion (not metadata-only)

`_delete_checkpoint_best_effort()` SHALL use `virsh checkpoint-delete` (without `--metadata` flag) as the primary deletion method. This removes both libvirt checkpoint metadata AND the QEMU internal dirty bitmap. If the full delete fails, a fallback to `virsh checkpoint-delete --metadata` SHALL be attempted. If both fail, a WARNING SHALL be logged.

#### Scenario: Full checkpoint delete succeeds

- **WHEN** `_delete_checkpoint_best_effort()` is called for a running VM
- **THEN** `virsh checkpoint-delete --domain <vm> <checkpoint>` is executed (no `--metadata`)
- **AND** both libvirt metadata and QEMU dirty bitmap are removed
- **AND** no "Bitmap already exists" collision on subsequent backup-begin

#### Scenario: Fallback to metadata-only when VM shut off

- **WHEN** full `checkpoint-delete` fails because the VM is shut off
- **THEN** `virsh checkpoint-delete --metadata --domain <vm> <checkpoint>` is attempted
- **AND** if the fallback succeeds, only libvirt metadata is removed
- **AND** a WARNING is logged if both methods fail


### Requirement: UUID suffix in checkpoint names

`_new_checkpoint_name(target_hash, disk, taken=None)` SHALL append a random 6-character hex suffix to checkpoint names. Format: `qsnap-{target_hash}-{disk}-{YYYYMMDDTHHMMSS}-{6_hex_chars}`. The suffix SHALL be generated via `secrets.token_hex(3)`. This prevents collisions when QEMU retains a bitmap that libvirt no longer tracks.

#### Scenario: Checkpoint name includes disk and UUID suffix

- **WHEN** `_new_checkpoint_name(target_hash="abcd1234", disk="vda")` is called
- **THEN** the returned name matches `qsnap-abcd1234-vda-YYYYMMDDTHHMMSS-<6_hex_chars>`
- **AND** the suffix is unique per call (via `secrets.token_hex`)

#### Scenario: Timestamp still parseable with suffix

- **WHEN** `_parse_checkpoint_timestamp()` is called with a name containing disk and UUID suffix
- **THEN** the timestamp is extracted correctly
- **AND** the regex `r"(\d{8}T\d{6})(?:-[0-9a-f]+)?"` matches the remainder after the prefix


### Requirement: "Bitmap already exists" collision recovery

When `virsh backup-begin` fails with an error containing "bitmap" and "exists" (case-insensitive), `transfer_missing()` SHALL call `_force_cleanup_checkpoints(vm_name, target_hash, disk)` to force-delete ALL qsnap checkpoints for this VM+target+disk. A new successor checkpoint name SHALL be generated (with a new UUID suffix). The backup-begin SHALL be retried once. If the retry also fails, the snapshot SHALL be marked as failed.

#### Scenario: Bitmap collision triggers force cleanup and retry

- **WHEN** `virsh backup-begin` fails with "Bitmap already exists"
- **THEN** `_force_cleanup_checkpoints(vm_name, target_hash, disk)` is called
- **AND** `candidates` are re-listed and `prior` re-selected
- **AND** a new successor checkpoint name is generated
- **AND** `virsh backup-begin` is retried with the new name
- **AND** if the retry succeeds, the transfer continues normally

#### Scenario: Force cleanup deletes all qsnap checkpoints for that disk

- **WHEN** `_force_cleanup_checkpoints()` is called
- **THEN** all checkpoints matching `qsnap-{target_hash}-{disk}-*` are deleted via `virsh checkpoint-delete` (full, not metadata-only)
- **AND** fallback to `--metadata` is attempted for each checkpoint that fails full delete


### Requirement: Size-based sanity check for incremental transfer

After incremental transfer, if the transferred bytes exceed 10× the expected delta upper bound, a WARNING SHALL be logged indicating a possible stale bitmap or write burst. The expected delta upper bound SHALL be the growth of the source disk's active-layer allocation since the last successful backup of this disk+target (from `_target_state.json` `last_backup_allocation`); when no baseline exists, the check SHALL be skipped. This is a diagnostic warning only — the transfer is not aborted. The check SHALL NOT reference snapshot allocation or snapshot timestamps.

#### Scenario: Large transfer triggers warning

- **WHEN** an incremental transfer transfers 15 GiB and the active-layer allocation grew by 100 MiB since the last backup of this disk
- **THEN** a WARNING is logged naming the disk, target, dirty bytes, and expected bound
- **AND** the transfer is NOT aborted (warning only)

#### Scenario: No baseline skips the check

- **WHEN** no `last_backup_allocation` baseline exists for this disk+target
- **THEN** the sanity check is skipped without a warning
