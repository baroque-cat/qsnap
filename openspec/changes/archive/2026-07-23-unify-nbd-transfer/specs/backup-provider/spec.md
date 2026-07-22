## MODIFIED Requirements

### Requirement: Transfer missing snapshots via dirty bitmap extraction

The system SHALL determine which snapshots are missing on the target and for each SHALL use `virsh backup-begin` with NBD export to transfer data. On first backup (no prior checkpoint), a full export is performed via the **unified NBD transfer engine** with `meta_contexts=["base:allocation"]` and `zero_skip=True`. On subsequent backups, only dirty blocks since the last checkpoint are exported via the unified engine with `meta_contexts=["base:allocation", "qemu:dirty-bitmap:backup-<disk>"]` and `zero_skip=False`. Every `backup-begin` SHALL receive a checkpoint XML as its third positional argument so the successor checkpoint is created atomically at the export's freeze point. **No `qemu-img convert` SHALL be used in the data path** — the unified engine uses `pread`/`pwrite` through `INbdClient`. The `full_verify_before_rebase` parameter is REMOVED from the `transfer_missing()` signature — it was dead plumbing (rebase died with file-copy).

#### Scenario: First backup — full NBD export (no prior checkpoint)

- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** the unified engine performs a full export with `meta_contexts=["base:allocation"]`, `zero_skip=True`
- **THEN** the backup is a standalone qcow2 file on the target containing the complete virtual disk
- **AND** no `qemu-img convert` is executed

#### Scenario: Incremental backup — dirty blocks only

- **WHEN** a prior qsnap checkpoint exists for this VM+target
- **AND** the VM has written data since that checkpoint
- **THEN** the unified engine transfers dirty∩allocated extents with `zero_skip=False`
- **THEN** the resulting backup file size is proportional to the changed data, not the full disk

#### Scenario: Checkpoint rotation after successful transfer

- **WHEN** the unified engine completes successfully and verification passes
- **THEN** the successor checkpoint created atomically with this export exists
- **THEN** all superseded (older) qsnap checkpoints are deleted via `virsh checkpoint-delete --metadata`
- **AND** exactly one qsnap checkpoint remains for this VM+target

#### Scenario: Transfer failure preserves prior checkpoint

- **WHEN** the transfer fails (NBD error or stall)
- **THEN** the prior checkpoint is NOT deleted
- **THEN** the successor checkpoint created by the failed run is deleted best-effort
- **THEN** the module returns `BackupResult(success=False, error=<message>)`
- **THEN** the NBD socket and qemu-nbd process are cleaned up

### Requirement: Backup verification step

`BitmapBackupProvider.transfer_missing()` SHALL perform post-transfer verification according to `target.verify`. `"off"` skips verification. Every verification failure SHALL produce `BackupResult(success=False, error="verification failed: ...")`. For incrementals, verification SHALL use `verify_bitmap_incremental()`. For full pulls (no prior checkpoint), verification SHALL use `verify_full_backup()`. The verify modes are `"off"`, `"metadata"`, `"compare"` (was `"hash"`/`"full"` — both ran `qemu-img compare`; now unified to `"compare"`). Existing configs with `verify="hash"` or `verify="full"` SHALL log a deprecation WARNING and be treated as `"compare"`.

#### Scenario: Metadata verification passes

- **WHEN** `target.verify` is `"metadata"` and the backup passes structural checks
- **THEN** backup is marked success

#### Scenario: Compare verification passes

- **WHEN** `target.verify` is `"compare"` and `qemu-img compare` succeeds
- **THEN** backup is marked success

#### Scenario: Verification failure produces error

- **WHEN** verification detects a structural or content mismatch
- **THEN** `BackupResult(success=False, error="verification failed: ...")` is returned

#### Scenario: Deprecated verify values treated as compare

- **WHEN** `target.verify` is `"hash"` or `"full"` (deprecated)
- **THEN** a WARNING is logged naming the deprecated value
- **AND** `"compare"` behavior is applied (qemu-img compare)
