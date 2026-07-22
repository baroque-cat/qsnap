## MODIFIED Requirements

### Requirement: Transfer missing snapshots via dirty bitmap extraction

The system SHALL determine which snapshots are missing on the target and for each SHALL use `virsh backup-begin` with NBD export to transfer data. On first backup (no prior checkpoint), a full export is performed via the **unified NBD transfer engine** with `meta_contexts=["base:allocation"]` and `zero_skip=True`. On subsequent backups, only dirty blocks since the last checkpoint are exported via the unified engine with `meta_contexts=["base:allocation", "qemu:dirty-bitmap:backup-<disk>"]` and `zero_skip=False`. Every `backup-begin` SHALL receive a checkpoint XML as its third positional argument so the successor checkpoint is created atomically at the export's freeze point. **No `qemu-img convert` SHALL be used in the data path** — the unified engine uses `pread`/`pwrite` through `INbdClient`. The `full_verify_before_rebase` parameter is REMOVED from the `transfer_missing()` signature — it was dead plumbing (rebase died with file-copy).

The FULL-pull scaffolding (qemu-img create, _start_write_server, _transfer, _terminate_qemu_nbd, mv .tmp → final, finally cleanup) SHALL be shared between `transfer_missing()` full-pull and `create_full_backup()` via a private `_full_pull_lifecycle()` helper method to eliminate ~200 lines of duplicated scaffolding code.

#### Scenario: First backup — full NBD export (no prior checkpoint)

- **WHEN** no prior qsnap checkpoint exists for this VM+target combination
- **THEN** the unified engine performs a full export with `meta_contexts=["base:allocation"]`, `zero_skip=True`
- **THEN** the backup is a standalone qcow2 file on the target containing the complete virtual disk
- **AND** no `qemu-img convert` is executed

#### Scenario: Incremental backup — dirty blocks via NBD checkpoint

- **WHEN** a prior checkpoint exists and VM has written data
- **THEN** the unified engine performs an incremental export with `meta_contexts=["base:allocation", "qemu:dirty-bitmap:backup-<disk>"]`, `zero_skip=False`
- **AND** only dirty∩allocated extents are transferred via `pread`/`pwrite`
- **AND** no `qemu-img convert` is executed

#### Scenario: Scaffolding dedup — both FULL paths use shared helper

- **WHEN** `transfer_missing()` full-pull or `create_full_backup()` executes a FULL backup
- **THEN** both SHALL call the private `_full_pull_lifecycle()` helper
- **AND** the helper handles: qemu-img create, _start_write_server, _transfer, _terminate_qemu_nbd, mv .tmp → final, finally cleanup
