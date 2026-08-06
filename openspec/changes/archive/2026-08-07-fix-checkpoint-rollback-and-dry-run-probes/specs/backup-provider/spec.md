## MODIFIED Requirements

### Requirement: BitmapBackupProvider.create_full_backup implemented via qemu-img convert

`BitmapBackupProvider` SHALL override `create_full_backup(vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, compress: bool = False, compression_type: str = "zstd", stall_timeout: int = 1800, convert_parallel: int = 4, convert_out_of_order: bool = True) -> BackupResult` to create a standalone FULL backup via `qemu-img convert`. `qemu-img convert` is the sole FULL backup transfer engine. The method SHALL use `qemu-img convert` via `_qemu_img_convert_transfer()`. The method SHALL NOT raise `NotImplementedError`. The result SHALL be a standalone qcow2 file on the target.

FULL backup naming format (multi-disk refactor): `{vm_name}.FULL.{YYYYMMDDTHHMMSS}_{disk}_{6hex}.qcow2`.

For running VMs, a checkpoint named `qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6hex}` SHALL be created atomically with the FULL's `backup-begin`, so a bitmap-mode FULL always leaves a checkpoint baseline anchored at the FULL's freeze point. For stopped VMs, no checkpoint is created.

The returned `BackupResult.checkpoint` SHALL carry the exact successor checkpoint name when a checkpoint was created (running-VM path, after `backup-begin` succeeds), and SHALL be `None` when no checkpoint was created (stopped-VM path, or `backup-begin` failure). This lets Core's rollback delete precisely the checkpoint the failed attempt created.

The method SHALL NOT call `self._state.record_full_backup()` — state recording is Core's responsibility after post-create verification passes.

#### Scenario: Bitmap FULL with zstd compression via qemu-img convert

- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd")` is called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 4 -W -p <source> <target>.tmp` is executed via `run_with_stall_detection()`
- **AND** the resulting FULL is compressed with zstd

#### Scenario: Bitmap FULL with custom convert_parallel

- **WHEN** `BitmapBackupProvider.create_full_backup("myvm", snapshot, target, compress=True, compression_type="zstd", convert_parallel=2)` is called
- **THEN** `qemu-img convert -c -O qcow2 -o compression_type=zstd -m 2 -W -p <source> <target>.tmp` is executed

#### Scenario: Bitmap FULL creates atomically with checkpoint (running VM)

- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully for a running VM
- **THEN** a backup-begin/freeze checkpoint named `qsnap-{target_hash}-{disk}-{yyyymmddTHHMMSS}-{6hex}` exists
- **AND** its baseline equals the FULL export's freeze point
- **AND** no standalone `virsh checkpoint-create-as` call is made by the provider

#### Scenario: No checkpoint for stopped VM FULL

- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully for a stopped VM
- **THEN** no checkpoint is created
- **AND** direct `qemu-img convert` is used from the source path

#### Scenario: Bitmap FULL does not self-record in state

- **WHEN** `BitmapBackupProvider.create_full_backup()` completes successfully
- **THEN** `self._state.record_full_backup()` is NOT called by the provider
- **AND** state recording is deferred to Core's `_backup_target()` after post-create verification

#### Scenario: Bitmap FULL with dotted VM name

- **WHEN** `BitmapBackupProvider.create_full_backup("3.Projects_opencode", snapshot, target, compress=False)` is called
- **THEN** the FULL backup file is named `3.Projects_opencode.FULL.YYYYMMDDTHHMMSS_{disk}_{6hex}.qcow2`

#### Scenario: Running-VM FULL reports its checkpoint name

- **WHEN** `create_full_backup()` completes successfully for a running VM
- **THEN** the returned `BackupResult.checkpoint` equals the successor checkpoint name created by `backup-begin`

#### Scenario: Stopped-VM FULL reports no checkpoint

- **WHEN** `create_full_backup()` completes successfully for a stopped VM
- **THEN** the returned `BackupResult.checkpoint` is `None`

#### Scenario: backup-begin failure reports no checkpoint

- **WHEN** `create_full_backup()` fails because `virsh backup-begin` returned non-zero (atomic — no checkpoint created)
- **THEN** the returned `BackupResult.checkpoint` is `None`
- **AND** `BackupResult.success` is `False`
