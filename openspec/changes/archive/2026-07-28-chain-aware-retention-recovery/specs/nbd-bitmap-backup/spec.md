## ADDED Requirements

### Requirement: Full checkpoint deletion (not metadata-only)

`_delete_checkpoint_best_effort()` SHALL use `virsh checkpoint-delete` (without `--metadata` flag) as the primary deletion method. This removes both libvirt checkpoint metadata AND the QEMU internal dirty bitmap. If the full delete fails (e.g., VM is shut off), a fallback to `virsh checkpoint-delete --metadata` SHALL be attempted. If both fail, a WARNING SHALL be logged.

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

`_new_checkpoint_name()` SHALL append a random 6-character hex suffix to checkpoint names. Format: `qsnap-{target_hash}-{YYYYMMDDTHHMMSS}-{6_hex_chars}`. The suffix SHALL be generated via `secrets.token_hex(3)`. This prevents collisions when QEMU retains a bitmap that libvirt no longer tracks.

#### Scenario: Checkpoint name includes UUID suffix

- **WHEN** `_new_checkpoint_name(target_hash="abcd1234")` is called
- **THEN** the returned name matches `qsnap-abcd1234-YYYYMMDDTHHMMSS-<6_hex_chars>`
- **AND** the suffix is unique per call (via `secrets.token_hex`)

#### Scenario: Timestamp still parseable with suffix

- **WHEN** `_parse_checkpoint_timestamp()` is called with a name containing a UUID suffix
- **THEN** the timestamp is extracted correctly (the suffix is after the timestamp)
- **AND** the regex `r"qsnap-([0-9a-f]{8})-(\d{8}T\d{6})(?:-[0-9a-f]+)?"` matches

### Requirement: "Bitmap already exists" collision recovery

When `virsh backup-begin` fails with an error containing "bitmap" and "exists" (case-insensitive), `transfer_missing()` SHALL call `_force_cleanup_checkpoints()` to force-delete ALL qsnap checkpoints for the VM+target. A new successor checkpoint name SHALL be generated (with a new UUID suffix). The backup-begin SHALL be retried once. If the retry also fails, the snapshot SHALL be marked as failed.

#### Scenario: Bitmap collision triggers force cleanup and retry

- **WHEN** `virsh backup-begin` fails with "Bitmap already exists"
- **THEN** `_force_cleanup_checkpoints(vm_name, target_hash)` is called
- **AND** a new successor checkpoint name is generated
- **AND** `virsh backup-begin` is retried with the new name
- **AND** if the retry succeeds, the transfer continues normally

#### Scenario: Force cleanup deletes all qsnap checkpoints

- **WHEN** `_force_cleanup_checkpoints()` is called
- **THEN** all checkpoints matching `qsnap-{target_hash}-*` are deleted via `virsh checkpoint-delete` (full, not metadata-only)
- **AND** fallback to `--metadata` is attempted for each checkpoint that fails full delete

### Requirement: Temporal mismatch detection

`transfer_missing()` SHALL skip snapshots whose timestamp predates the newest checkpoint's creation time. The checkpoint timestamp SHALL be parsed from the checkpoint name via `_parse_checkpoint_timestamp()`. The snapshot timestamp SHALL come from `SnapshotInfo.timestamp`. If `snapshot_ts < checkpoint_ts`, the snapshot SHALL be skipped with `BackupResult(success=False, error="temporal_mismatch_skipped")` and a WARNING log.

#### Scenario: Snapshot predating checkpoint is skipped

- **WHEN** the newest checkpoint was created at 2026-07-27T0106
- **AND** a snapshot has timestamp 2026-07-27T0008 (before the checkpoint)
- **THEN** the snapshot is skipped
- **AND** `BackupResult(success=False, error="temporal_mismatch_skipped")` is returned
- **AND** a WARNING log explains the temporal mismatch

#### Scenario: Snapshot after checkpoint proceeds normally

- **WHEN** the newest checkpoint was created at 2026-07-27T0106
- **AND** a snapshot has timestamp 2026-07-27T0200 (after the checkpoint)
- **THEN** the snapshot is transferred normally (no temporal mismatch)

### Requirement: Size-based sanity check for temporal mismatch

After incremental transfer, if the transferred bytes exceed 10x the snapshot's allocation size, a WARNING SHALL be logged indicating possible temporal mismatch. This is a diagnostic warning only — the transfer is not aborted.

#### Scenario: Large transfer triggers warning

- **WHEN** an incremental transfer transfers 15 GiB for a snapshot with allocation 100 MiB
- **THEN** a WARNING is logged: "transferred 15 GiB for snapshot (allocation=100 MiB) — ratio 153x exceeds threshold, possible temporal mismatch"
- **AND** the transfer is NOT aborted (warning only)
