# Bitmap Loss Recovery

## Purpose

Self-healing for checkpoint dirty bitmaps lost after an unclean host shutdown (QEMU discards unsynced bitmaps while libvirt checkpoint metadata survives). Detects dead checkpoint bitmaps before a delta is attempted and recovers autonomously: a recovered delta when gates G1-G3 prove it safe, otherwise a FULL fallback. Guarantees via the reactive backstop that a dead checkpoint can never fail two runs in a row, and that a successful recovery exits 0 with a WARNING.

## Requirements

### Requirement: Crash evidence collection and WARNING semantics

The system SHALL collect read-only evidence explaining a dead checkpoint bitmap and SHALL
report it via WARNING log lines. Evidence SHALL include: (a) the bitmap probe verdict,
(b) whether a backup file covering the checkpoint's freeze timestamp exists on the target,
and (c) whether the host `boot_id` changed since the last successful run (capability
`state-management`). Evidence SHALL inform log wording only — recovery SHALL be triggered
by the DEAD probe verdict alone, regardless of the diagnosed cause. A fully successful
recovery (either branch) SHALL result in exit code 0; recovery is designed behavior, not an
abort. Exit code 10 (`BackupAbortError`) SHALL occur only when recovery itself is exhausted
(recovered-delta attempt fails AND the FULL fallback fails).

#### Scenario: Unclean shutdown evidence logged

- **WHEN** the probe returns DEAD, a covering backup file exists, and the boot_id changed
  since the last successful run
- **THEN** qsnap logs a WARNING naming the checkpoint, the disk, and "unclean host shutdown
  detected"
- **AND** recovery proceeds

#### Scenario: Successful recovery exits zero

- **WHEN** recovery completes (recovered delta or FULL fallback) and verification passes
- **THEN** the run exits 0
- **AND** the WARNING is the only error-severity output related to the incident

#### Scenario: Dead bitmap without boot change still recovers

- **WHEN** the probe returns DEAD but the boot_id is unchanged or unknown
- **THEN** recovery proceeds identically
- **AND** the WARNING does not attribute a cause it cannot prove

### Requirement: Recovery gates G1-G3

A recovered delta SHALL be built only when all three gates pass; otherwise the recovery
path SHALL fall back to FULL. **G1 (no commit since freeze):** the per-disk
`last_commit_ts` state marker (capability `state-management`) SHALL be strictly earlier
than the checkpoint's freeze timestamp; an absent marker SHALL fail the gate
(conservative). **G2 (chain matches state):** the live backing chain of the disk SHALL
contain every post-freeze snapshot recorded in state, in order. **G3 (overlays readable):**
every layer of the copy set SHALL be readable (`qemu-img info` succeeds). Gate evaluation
SHALL be read-only.

#### Scenario: Commit after checkpoint freeze fails G1

- **WHEN** `last_commit_ts` for the disk is later than the checkpoint freeze timestamp
- **THEN** the recovered delta is skipped and FULL is used
- **AND** the prediction/log names the failed gate

#### Scenario: Absent commit marker fails G1

- **WHEN** state holds no `last_commit_ts` for the disk (pre-feature state file)
- **THEN** G1 fails and FULL is used

#### Scenario: All gates pass

- **WHEN** G1, G2, and G3 all pass
- **THEN** the recovered delta path is taken

### Requirement: Copy set computation

The recovered-delta copy set `S` SHALL be the overlay that was active at the checkpoint
freeze (newest snapshot with timestamp ≤ freeze-ts) plus every overlay created after the
freeze. Snapshot timestamps SHALL be read from per-VM snapshot state — this is the scoped
orthogonality exception codified in capability `backup-target-orthogonality`. If snapshot
state is incomplete or unavailable, `S` SHALL fall back to all overlays above `base_image`
(a larger but still correct superset).

#### Scenario: Copy set from state timestamps

- **WHEN** the checkpoint froze at T and state lists snapshots with timestamps around T
- **THEN** S contains exactly the newest snapshot with timestamp ≤ T and all snapshots with
  timestamp > T

#### Scenario: Incomplete state falls back to full overlay set

- **WHEN** snapshot state lacks entries needed to bound S
- **THEN** S is all overlays above `base_image`
- **AND** recovery still proceeds

### Requirement: Recovered delta lifecycle

The recovered delta SHALL execute as follows: (1) create the successor checkpoint via
`virsh checkpoint-create` with the standard checkpoint XML, establishing the atomic freeze
point T' and the successor bitmap, without starting a backup job; (2) create the target
file `qemu-img create -f qcow2 -b <newest existing backup of this disk> -F qcow2 <tmp>`;
(3) serve `<tmp>` with the existing write-server mechanism; (4) for each layer of S from
oldest to newest, serve the layer read-only and copy ALL data and zero extents into
`<tmp>`, skipping only holes; (5) flush, stop the writer, publish via `mv` to the
freeze-timestamp name `{vm}.{T'}_{disk}_{hex6}.qcow2`; (6) verify that the backing chain
resolves to the FULL anchor and that `qemu-img check` passes. Zero extents SHALL be copied
explicitly because guest discards are represented as zero clusters and skipping them would
expose stale backing data. On any failure the provider SHALL delete the successor
checkpoint best-effort, remove `<tmp>`, and fall back to FULL within the same run. On
success the dead checkpoint SHALL be deleted (full delete, `--metadata` fallback) and the
provider SHALL return `BackupResult(success=True, kind="recovered_delta")`.

#### Scenario: Successful recovered delta

- **WHEN** all gates pass and the transfer and verification succeed
- **THEN** the new backup chains onto the newest existing target backup
- **AND** the successor checkpoint exists with a healthy bitmap
- **AND** the dead checkpoint is deleted
- **AND** the old backup chain (FULL and incrementals) is NOT deleted

#### Scenario: Zero extents are copied

- **WHEN** a layer in S contains zero clusters (guest discards)
- **THEN** those extents are written as explicit zeros into the recovered delta
- **AND** holes are the only skipped extents

#### Scenario: Transfer failure rolls back and falls back to FULL

- **WHEN** the recovered-delta transfer or verification fails
- **THEN** the successor checkpoint is deleted best-effort and `<tmp>` removed
- **AND** a FULL backup is attempted in the same run

#### Scenario: Consistency under concurrent guest writes

- **WHEN** the guest writes blocks while the recovered delta copies layer extents
- **THEN** every write after T' is recorded in the successor bitmap
- **AND** any block copied stale or torn is re-copied by the next regular delta

### Requirement: FULL fallback with post-verification retirement

When the FULL branch of recovery executes (gates failed or recovered delta failed), the
system SHALL enforce this order: create the FULL via the standard mechanism → pass M1/M2
verification inside `run_backup` → only then delete the dead checkpoint and retire the
superseded generation. The retirement SHALL remove the old FULL and all its incrementals
regardless of `keep_generations` (recovery path exception, capability
`per-chain-retention`), while the deleted FULL SHALL still pass the existing
verify-before-delete gates (M1 always, M2 per `full_verify_before_delete`). In the
recovered-delta branch no generation is retired.

#### Scenario: Old generation retired only after new FULL verified

- **WHEN** the recovery FULL passes M1/M2 verification
- **THEN** the dead checkpoint is deleted and the superseded generation (old FULL plus its
  incrementals) is removed
- **AND** the verify-before-delete gates were applied to the deleted FULL

#### Scenario: Failed recovery FULL preserves the old generation

- **WHEN** the recovery FULL fails creation or verification
- **THEN** the old generation and all backup files remain untouched
- **AND** the run reports the backup failure via the existing abort path

### Requirement: Reactive backstop for checkpoint-inconsistent errors

When `virsh backup-begin` fails with an error containing "checkpoint inconsistent" (the
probe said HEALTHY or UNKNOWN, or raced), the provider SHALL delete exactly the named
checkpoint and retry once: recovered delta when the gates pass, otherwise FULL. This
backstop SHALL guarantee that a dead checkpoint can never produce the same failure twice in
a row. The existing "bitmap already exists" collision recovery SHALL remain unchanged.

#### Scenario: Backstop heals a probe miss

- **WHEN** the probe returned UNKNOWN and `backup-begin` fails with "checkpoint
  inconsistent: missing or broken bitmap '<name>'"
- **THEN** checkpoint `<name>` is deleted
- **AND** the backup is retried once as recovered delta (gates pass) or FULL (gates fail)
- **AND** the second failure, if any, follows the normal failure path

#### Scenario: No infinite failure loop

- **WHEN** a system with a dead checkpoint runs qsnap repeatedly
- **THEN** the first run heals (recovered delta or FULL) and exits 0
- **AND** the second run performs a normal delta with no warnings

### Requirement: Startup invariant treats dead-bitmap checkpoints as orphans

The startup orphan-checkpoint invariant (capability `startup-state-validation`) SHALL treat
a checkpoint whose bitmap probe returns DEAD as an orphan even when a covering backup file
exists. In real runs the dead checkpoint SHALL be deleted best-effort with a WARNING; in
dry-run it SHALL be reported without mutation. Deleting a dead checkpoint destroys nothing:
its bitmap no longer exists.

#### Scenario: Dead checkpoint with covering file removed at startup

- **WHEN** startup validation finds the newest checkpoint covered by a backup file but its
  bitmap is DEAD
- **THEN** the checkpoint is deleted best-effort with a WARNING in a real run
- **AND** the next backup for that disk is a FULL
