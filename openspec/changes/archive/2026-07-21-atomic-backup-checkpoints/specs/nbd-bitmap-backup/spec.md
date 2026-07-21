# Delta: nbd-bitmap-backup

## REMOVED Requirements

### Requirement: Checkpoint-only creation when FULL exists and no prior checkpoint

**Reason**: The checkpoint-only path (design D4) created the baseline checkpoint *after* the FULL export finished, while the FULL is frozen at its `backup-begin` start. Guest writes inside that window were covered by neither the FULL nor any later incremental — a silent restore hole. The path also depended on state-recording timing (`get_full_backups()` already containing the FULL), which proved fragile in production. Atomic checkpoint creation (this change) makes the FULL run leave a valid baseline by construction, so the checkpoint-only shortcut is unnecessary.

**Migration**: No user action required. Checkpoints previously created via `virsh checkpoint-create-as` remain valid baselines and are discovered by the newest-wins lookup. When `transfer_missing()` finds no prior checkpoint on a target that already has FULLs, the behavior reverts to a full NBD export (safe: redundant transfer, never data loss).

## MODIFIED Requirements

### Requirement: NBD pull-model backup via virsh backup-begin

`BitmapBackupProvider` v2 SHALL use the libvirt pull-model backup API instead of `qemu-img convert --bitmap`. The transfer pipeline SHALL be: (1) create backup XML with NBD Unix socket, (2) create checkpoint XML naming the successor checkpoint, (3) `virsh backup-begin --domain VM backup.xml checkpoint.xml` to start the NBD export and atomically create the successor checkpoint at the export's freeze point, (4) `qemu-img convert -n nbd:unix:<socket> <target>` to pull dirty blocks, (5) cleanup socket. Checkpoints SHALL persist for subsequent incremental runs.

The incremental checkpoint SHALL be passed via an `<incremental>` element in the backup XML, NOT via a `--incremental` CLI flag. The `--incremental` flag does not exist in any version of virsh `backup-begin`. The `write_backup_xml()` function SHALL accept an optional `incremental: str | None = None` parameter. When non-None, the XML SHALL include `<incremental>{checkpoint_name}</incremental>` as a child of `<domainbackup>`, before the `<server>` element.

The successor checkpoint SHALL be passed as a separate checkpoint XML file given as the third positional argument to `virsh backup-begin`. A new `write_checkpoint_xml(checkpoint_name: str) -> Path` function in `qsnap/utils/nbd.py` SHALL generate it as `<domaincheckpoint><name>{checkpoint_name}</name></domaincheckpoint>` in a temp file. Both XML temp files SHALL be removed after the run regardless of outcome.

#### Scenario: First backup — full pull via NBD with atomic checkpoint

- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** `write_backup_xml(socket_path, incremental=None)` is called
- **THEN** the backup XML does NOT contain an `<incremental>` element
- **THEN** `virsh backup-begin --domain VM backup.xml checkpoint.xml` starts a full NBD export (no `--incremental` CLI flag)
- **AND** the successor checkpoint is created atomically at the export's freeze point
- **THEN** `qemu-img convert -n nbd:unix:<socket> <target>` creates a standalone qcow2 file on target

#### Scenario: Incremental backup — dirty blocks via NBD checkpoint

- **WHEN** a prior checkpoint exists and VM has written data
- **THEN** `write_backup_xml(socket_path, incremental=prior_checkpoint)` is called
- **THEN** the backup XML contains `<incremental>prior_checkpoint</incremental>`
- **THEN** `virsh backup-begin --domain VM backup.xml checkpoint.xml` exports only blocks changed since the checkpoint
- **AND** a new successor checkpoint is created atomically at this export's freeze point
- **THEN** `qemu-img convert` pulls only dirty blocks, producing a smaller backup
- **AND** no `--incremental` CLI flag is passed to `virsh backup-begin`

#### Scenario: Socket cleanup on success

- **WHEN** `qemu-img convert` completes successfully
- **THEN** the Unix socket is removed via `rm -f`
- **THEN** the atomically created successor checkpoint is preserved as the baseline for the next incremental run

#### Scenario: Socket cleanup on failure

- **WHEN** `qemu-img convert` fails (non-zero exit or timeout)
- **THEN** the Unix socket is still removed via `rm -f` in a finally block
- **THEN** `BackupResult(success=False, ...)` is returned
- **AND** the prior checkpoint is preserved
- **AND** the successor checkpoint created by this failed run is deleted best-effort via `virsh checkpoint-delete --metadata`

### Requirement: BitmapBackupProvider.create_full_backup via NBD full export

`BitmapBackupProvider` SHALL implement `create_full_backup()` using the NBD full-export path (no `--incremental` flag). This produces a standalone qcow2 on the target. The method SHALL NOT raise `NotImplementedError`. The method SHALL pass a `checkpoint_name` to `nbd_full_export()` so that a baseline checkpoint is created **atomically** with the FULL's `backup-begin`, named `qsnap-{target_hash}-{yyyymmddTHHMMSS}`. A bitmap-mode FULL therefore always leaves a checkpoint baseline anchored at the FULL's freeze point. When `compress=True` and `compression_type="zstd"`, the `-c -o compression_type=zstd` flags SHALL be passed to `qemu-img convert` in the NBD path. When `compress=True` and `compression_type="zlib"`, only `-c` SHALL be added. The `compression_type` parameter SHALL be passed through to `nbd_full_export()`.

#### Scenario: Bitmap FULL with zstd compression

- **WHEN** `BitmapBackupProvider.create_full_backup(snapshot, target, compress=True, compression_type="zstd", bucket_level="monthly")` is called
- **THEN** `qemu-img convert -c -o compression_type=zstd nbd:unix:<socket> <target>` is called
- **AND** the resulting FULL is compressed with zstd

#### Scenario: Bitmap FULL with zlib compression

- **WHEN** `BitmapBackupProvider.create_full_backup(snapshot, target, compress=True, compression_type="zlib", bucket_level="monthly")` is called
- **THEN** `qemu-img convert -c nbd:unix:<socket> <target>` is called (default zlib)
- **AND** the resulting FULL is compressed with zlib

#### Scenario: Bitmap FULL socket cleanup

- **WHEN** the NBD full export completes (success or failure)
- **THEN** the Unix socket is removed via `rm -f` in a `finally` block

#### Scenario: Bitmap FULL leaves an atomic checkpoint baseline

- **WHEN** `BitmapBackupProvider.create_full_backup()` is called for a running VM
- **THEN** `virsh backup-begin` is invoked with a checkpoint XML as the third positional argument
- **AND** on success a checkpoint named `qsnap-{target_hash}-{yyyymmddTHHMMSS}` exists
- **AND** its baseline equals the FULL export's freeze point

#### Scenario: Bucket-driven FULL no longer crashes bitmap targets

- **WHEN** `Core._backup_target()` triggers `_should_create_bucket_full()` for a bitmap-mode target
- **AND** it returns `(True, bucket_level)`
- **THEN** `BitmapBackupProvider.create_full_backup()` is called and succeeds
- **AND** the FULL is recorded in state with the given `bucket_level`

### Requirement: Libvirt version check for NBD API

`DefaultFactory.create_backup_provider()` SHALL call `is_libvirt_new_enough(shell)` from `qsnap.utils.nbd` before constructing `BitmapBackupProvider`. If the version is insufficient, the factory SHALL log a WARNING and return `FileCopyBackupProvider`. `BitmapBackupProvider.__init__()` SHALL NOT perform version checking — it SHALL NOT call `virsh --version` and SHALL NOT raise `RuntimeError` for an old libvirt.

`is_libvirt_new_enough()` SHALL return `True` only for libvirt version 7.2 or newer. The incremental backup API (including the `<incremental>` XML element and the checkpoint XML argument of `backup-begin` exercised by this change) is complete since libvirt 7.2 per the libvirt knowledge base; the previous 6.0 threshold was insufficient.

#### Scenario: Libvirt too old

- **WHEN** `virsh --version` returns a version older than 7.2
- **THEN** `is_libvirt_new_enough(shell)` returns `False`
- **THEN** `DefaultFactory` does NOT construct `BitmapBackupProvider`
- **THEN** `DefaultFactory` logs a WARNING and returns `FileCopyBackupProvider(shell, state)`

#### Scenario: Libvirt sufficient

- **WHEN** `virsh --version` returns a version 7.2 or newer
- **THEN** `is_libvirt_new_enough(shell)` returns `True`
- **THEN** `DefaultFactory` constructs and returns `BitmapBackupProvider(shell)`

#### Scenario: BitmapBackupProvider constructor is version-check-free

- **WHEN** `BitmapBackupProvider(shell)` is instantiated
- **THEN** no `virsh --version` shell call is made in `__init__`
- **AND** no `RuntimeError` is raised for version reasons
- **AND** the only parameter is `shell: IShell`

## ADDED Requirements

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

- **WHEN** `qemu-img convert` or verification fails during an incremental export
- **THEN** the prior checkpoint is NOT deleted
- **AND** the successor checkpoint created by the failed run is deleted best-effort
- **AND** the newest remaining qsnap checkpoint is the pre-run baseline

#### Scenario: checkpoint-delete failure is non-fatal

- **WHEN** `virsh checkpoint-delete --metadata` for a superseded checkpoint returns non-zero
- **THEN** a WARNING is logged with the checkpoint name and error
- **AND** the `BackupResult` remains `success=True`
- **AND** the stale checkpoint is retried for deletion on the next successful run

### Requirement: First incremental after FULL transfers dirty blocks since FULL start

The first `transfer_missing()` incremental after a bitmap-mode FULL SHALL export all blocks dirtied since the FULL export's freeze point, because the FULL's atomically created checkpoint is the baseline. This transfer is the true delta and SHALL NOT be skipped or replaced by a checkpoint-only no-op. The transfer size is bounded by guest write rate multiplied by FULL duration plus the time since the FULL.

#### Scenario: Writes during FULL export appear in the first incremental

- **WHEN** a FULL export runs while the guest writes data
- **AND** `transfer_missing()` runs afterwards in the same pipeline run
- **THEN** the first incremental export contains the blocks written during the FULL export
- **AND** the resulting target file is non-empty and passes verification

#### Scenario: No writes since FULL — minimal incremental

- **WHEN** the guest wrote nothing between the FULL's freeze point and the first incremental export
- **THEN** the incremental export completes successfully with a near-empty payload
- **AND** the checkpoint rotation still occurs
