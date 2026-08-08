# Backup Target Orthogonality — delta

## MODIFIED Requirements

### Requirement: Backup phase SHALL NOT consume snapshot data

The backup (target) world SHALL be self-contained. `Core._backup_target()` and everything it invokes SHALL NOT read snapshot names, snapshot timestamps, snapshot file paths, or the snapshot list from `IStateManager`. The only inputs to the backup phase SHALL be: `VMConfig`, `TargetConfig`, the backup provider's own discovery (target file listing, libvirt checkpoints), and target-scoped state (`_target_state.json`, `_full_backups.json`, `_dependencies.json`). Stale snapshot-file healing belongs to the snapshot world, not to the backup provider.

Scoped exception: the bitmap-loss recovery path (capability `bitmap-loss-recovery`) MAY read snapshot timestamps from per-VM state for the single purpose of computing the recovered-delta copy set (the overlay active at the checkpoint freeze plus overlays created after). This exception applies only when the newest checkpoint's bitmap is DEAD, reads timestamps only (never snapshot contents), and SHALL NOT be used by the normal FULL/delta path under any condition.

#### Scenario: Backup phase runs with zero snapshots in state

- **WHEN** a VM has no snapshots in state and no snapshot files on disk
- **AND** `backup_create = "always"` for a target
- **THEN** FULL and subsequent delta backups are created for every configured disk
- **AND** no error or skip referencing snapshots occurs

#### Scenario: Provider receives no SnapshotInfo

- **WHEN** Core invokes any `IBackupProvider` method for backup creation, listing, or deletion
- **THEN** no `SnapshotInfo` object is passed or returned anywhere in the call chain

#### Scenario: Normal path never reads snapshot state

- **WHEN** the newest checkpoint is HEALTHY or absent
- **THEN** the backup path performs no snapshot-state reads of any kind

#### Scenario: Recovery path reads timestamps only

- **WHEN** the recovery path computes the copy set from snapshot state
- **THEN** it reads snapshot timestamps only
- **AND** the result is used exclusively to bound the recovered-delta copy set

### Requirement: Checkpoint is the sole delta baseline

Delta baselines SHALL be selected newest-wins from `virsh checkpoint-list` filtered by the `qsnap-{target_hash}-{disk}-` prefix. `IStateManager` and snapshot data SHALL NOT be consulted for baseline selection. No comparison between checkpoint timestamps and snapshot timestamps SHALL exist anywhere in the backup path. A checkpoint SHALL qualify as a usable delta baseline only while its dirty bitmap is healthy (capability `checkpoint-bitmap-health-probe`): a checkpoint whose bitmap is missing or inconsistent is not a baseline and SHALL be routed to bitmap-loss recovery (capability `bitmap-loss-recovery`) instead of a delta export.

#### Scenario: Baseline discovery uses only libvirt checkpoints

- **WHEN** `run_backup` selects a baseline for a delta
- **THEN** the selection input is exclusively the checkpoint name list for this VM+target+disk
- **AND** no snapshot timestamp participates in any comparison

#### Scenario: Dead checkpoint is not a baseline

- **WHEN** the newest checkpoint's bitmap probe returns DEAD
- **THEN** no delta export is started against it
- **AND** the recovery path decides the backup kind
