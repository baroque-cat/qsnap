# Dry-Run Prediction — delta

## REMOVED Requirements

### Requirement: Backup steps evaluated with simulated snapshots

**Reason:** The backup phase no longer consumes snapshot data (capability
`backup-target-orthogonality`). FULL/delta decisions depend only on target-internal data
(checkpoints, dependency counts, onchange gate), so there is nothing to merge simulated
snapshots into.

**Migration:** Backup predictions are derived from target-internal data per the modified
requirements below. Snapshot predictions (creation, retention, blockcommit) are unchanged and
still use simulated snapshots.

## MODIFIED Requirements

### Requirement: Backup prediction from target-internal data

In dry-run mode, `Core._backup_target()` SHALL predict the backup that a real run would
create for each disk of each target, using only target-internal data: the onchange gate
state, the presence of a checkpoint for the disk (`list_checkpoints`), and the FULL/delta
decision (dependency count vs `target_chain_length`). When the gate is open, Core SHALL log
one INFO prediction per disk: "FULL will be created" (no checkpoint or FULL due) or "delta
will be created since checkpoint <name>" (checkpoint exists), with the target path and an
approximate size estimate. Predictions SHALL NOT reference snapshot names and SHALL NOT
predict per-snapshot transfer lists. Estimates are upper bounds and SHALL be presented as
approximate.

#### Scenario: Gate open with checkpoint predicts one delta per disk

- **WHEN** dry-run evaluates a disk with an existing checkpoint and an open gate
- **THEN** exactly one prediction is emitted: delta since the newest checkpoint, with target
  and approximate size
- **AND** no NBD export, checkpoint, or file write occurs

#### Scenario: Gate closed predicts no backup

- **WHEN** the onchange gate is closed for a disk
- **THEN** no backup prediction is emitted for that disk

#### Scenario: No checkpoint predicts FULL

- **WHEN** no checkpoint exists for the disk and the gate is open
- **THEN** the prediction is "FULL will be created" regardless of snapshot state

### Requirement: FULL backup prediction with size estimate

In dry-run mode, when the FULL/delta decision determines a FULL would be created, Core SHALL
log an INFO prediction containing the disk target, the transfer method, the VM running state,
and an estimated standalone size computed read-only from the disk's `base_image` backing
chain (`qemu-img info --force-share --backing-chain --output=json`) — a real FULL exports the
live disk plus a near-zero fresh overlay, so the base chain is the correct estimate source
for both running and stopped VMs. The chain-size estimation logic SHALL be shared with
`Core.fork()` via a single helper. The same estimate SHALL feed the dry-run free-space gate
so prediction and gate never disagree. Estimation probe failures SHALL NOT log above DEBUG.
When the estimation command fails, the prediction SHALL still be emitted with size unknown.

#### Scenario: FULL prediction carries chain size estimate

- **WHEN** dry-run predicts a FULL for disk `vda` whose `base_image` backing chain sums to
  1 GiB of `actual-size`
- **THEN** the prediction log includes the disk, method, VM state, and an approximate size of
  1 GiB

#### Scenario: Estimation failure degrades gracefully

- **WHEN** the `qemu-img info --backing-chain` call fails during dry-run
- **THEN** the FULL prediction is still logged, with the size marked unknown
- **AND** the pipeline does not abort

#### Scenario: Estimation never uses snapshot files

- **WHEN** dry-run estimates a FULL size
- **THEN** the estimate source is the disk's `base_image` backing chain
- **AND** no snapshot file path participates in the estimation
