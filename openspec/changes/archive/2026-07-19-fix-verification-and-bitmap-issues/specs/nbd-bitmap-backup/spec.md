## ADDED Requirements

### Requirement: Checkpoint-only creation when FULL exists and no prior checkpoint

`BitmapBackupProvider.transfer_missing()` SHALL check `self._state.get_full_backups(str(target.path))` when no prior checkpoint is found (`prior_checkpoints` is empty). If FULLs exist in state, the provider SHALL create a checkpoint via `virsh checkpoint-create-as --domain <vm_name> --name qsnap-{target_hash}-{snapshot_name}` without performing a data transfer, then `continue` to the next snapshot. This avoids a redundant full NBD export when the bucket strategy already created a FULL in the same run. The FULL already contains all data at this point in time; the checkpoint serves as the baseline for the next incremental run.

If `self._state` is `None` or no FULLs exist in state, the existing behavior SHALL be preserved: a full NBD export is performed (no `--incremental` flag).

#### Scenario: Checkpoint created without transfer when FULL exists

- **WHEN** `transfer_missing()` is called and no prior checkpoint exists for this VM+target
- **AND** `self._state.get_full_backups(target_path)` returns a non-empty list
- **THEN** `virsh checkpoint-create-as --domain <vm> --name qsnap-{hash}-{snap}` is called
- **AND** no `virsh backup-begin` is called (no data transfer)
- **AND** no `qemu-img convert` is called
- **AND** the snapshot is skipped (no `BackupResult` appended for it)
- **AND** an INFO log is emitted: "Created checkpoint %s without transfer (FULL exists in state)"

#### Scenario: Full NBD export when no FULL and no checkpoint

- **WHEN** `transfer_missing()` is called and no prior checkpoint exists
- **AND** `self._state.get_full_backups(target_path)` returns an empty list (or `self._state` is `None`)
- **THEN** the existing behavior is preserved: `virsh backup-begin` is called without `--incremental`
- **AND** a full NBD export is performed

#### Scenario: Checkpoint-only path does not trigger when checkpoint exists

- **WHEN** `transfer_missing()` is called and a prior checkpoint exists
- **THEN** the existing incremental path is used (`virsh backup-begin --incremental <prior>`)
- **AND** the FULL-existence check is not performed (short-circuited by `prior is not None`)

#### Scenario: Checkpoint-only path skips snapshots already on target

- **WHEN** `transfer_missing()` is called and the snapshot name already exists on the target
- **THEN** the snapshot is skipped before reaching the checkpoint-only logic
- **AND** no checkpoint is created for it

### Requirement: Compression for NBD incremental transfers

`BitmapBackupProvider.transfer_missing()` SHALL pass the `-c` flag to `qemu-img convert` when `target.compress=True` (default). This compresses the output qcow2 file using zlib per-cluster compression, matching the existing FULL backup compression behavior. When `target.compress=False`, no `-c` flag SHALL be added.

#### Scenario: Incremental transfer with compression

- **WHEN** `transfer_missing()` is called with `target.compress=True`
- **THEN** the `qemu-img convert` command SHALL include the `-c` flag
- **AND** the resulting qcow2 file SHALL have compressed data clusters

#### Scenario: Incremental transfer without compression

- **WHEN** `transfer_missing()` is called with `target.compress=False`
- **THEN** the `qemu-img convert` command SHALL NOT include the `-c` flag
- **AND** the resulting qcow2 file SHALL have uncompressed data clusters

#### Scenario: Compression does not affect metadata verification

- **WHEN** a compressed incremental backup is verified with `verify="metadata"`
- **THEN** `qemu-img info` reports the same `format` and `virtual-size` as an uncompressed backup
- **AND** verification passes (compression does not affect metadata fields)

#### Scenario: Compression does not affect full verification

- **WHEN** a compressed incremental backup is verified with `verify="full"`
- **THEN** `qemu-img compare` decompresses clusters during comparison
- **AND** verification compares virtual disk content correctly (compression is transparent)
