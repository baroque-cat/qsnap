# Dry-Run Prediction

## Purpose

Defines dry-run prediction behavior: simulated future snapshots threaded through retention and backup decisions, incremental transfer and FULL predictions with approximate size estimates, per-disk blockcommit and deferred-drain predictions, the zero-mutation invariant, and the structured predictions channel carried by `PipelineResult.predictions`.
## Requirements
### Requirement: Simulated future snapshots in dry-run

In dry-run mode, when the snapshot gate passes (always mode, or onchange mode with at least one changed disk), `Core._create_snapshot()` SHALL build one simulated `SnapshotInfo` per configured disk instead of returning an empty list. Each simulated snapshot SHALL carry: a predicted name produced by the real `_generate_snapshot_name()` function, the path `<effective snapshot_dir>/<name>.qcow2`, `timestamp` = current time, `allocation` = the disk's current allocation obtained read-only via `IChangeDetector.has_changed()`, and `disk` = the disk target. Simulated snapshots SHALL NOT be written to `IStateManager`, and no `virsh snapshot-create-as` command SHALL be executed. Each simulated snapshot SHALL be logged at INFO level with VM and disk context and its allocation estimate. Predicted names are illustrative — a later real run produces its own timestamp and hex suffix.

#### Scenario: Multi-disk VM produces per-disk simulated snapshots
- **WHEN** `qsnap -n run` is executed for a VM with disks `vda` and `vdb` in snapshot-always mode
- **THEN** one simulated `SnapshotInfo` is produced for `vda` and one for `vdb`
- **AND** each is logged at INFO with the VM name, disk target, predicted name, and allocation estimate
- **AND** `IStateManager.record_snapshot()` is never called

#### Scenario: Onchange gate closed produces no simulated snapshots
- **WHEN** `qsnap -n run` is executed in onchange mode and no disk has changed
- **THEN** no simulated snapshots are produced and no snapshot prediction is logged

#### Scenario: Simulated snapshot allocation comes from read-only detection
- **WHEN** a simulated snapshot is built for disk `vda`
- **THEN** its `allocation` equals `ChangeResult.current_allocation` returned by the change detector
- **AND** no mutating shell command is executed to obtain it

### Requirement: Retention prediction against post-run state

In dry-run mode, `Core._evaluate_snapshot_retention()` SHALL merge the simulated snapshots (Requirement: Simulated future snapshots in dry-run) with the snapshots read from `IStateManager` before per-disk grouping and retention evaluation. The predicted keep/remove split SHALL reflect the post-run world. In non-dry-run mode the merge input SHALL be absent and behavior SHALL be unchanged.

#### Scenario: Retention counts the would-be-created snapshot
- **WHEN** disk `vda` has 2 snapshots in state, `snapshot_chain_length = 2`, and dry-run simulates a third snapshot
- **THEN** the retention prediction marks the oldest existing snapshot for removal
- **AND** the simulated snapshot is in the keep set

#### Scenario: Real run behavior unchanged
- **WHEN** the pipeline runs with dry-run disabled
- **THEN** retention evaluation reads only `IStateManager` snapshots, exactly as before this change

### Requirement: Backup prediction from target-internal data

In dry-run mode, `Core._backup_target()` SHALL predict the backup that a real run would create for each disk of each target, using the provider's read-only baseline assessment (capability `checkpoint-bitmap-health-probe`): the onchange gate state, the presence and bitmap health of the newest checkpoint, the recovery gate outcome when the checkpoint is dead, and the FULL/delta decision (dependency count vs `target_chain_length`). Dry-run SHALL execute every read-only check the real path performs before its point of no return — including the bitmap health probe and the blockjob probe — and SHALL skip only mutations. When the gate is open, Core SHALL log one INFO prediction per disk: "FULL will be created" (no checkpoint or FULL due), "delta will be created since checkpoint <name>" (healthy checkpoint), "recovered-delta will be created (~size, allocation superset since <freeze-ts>; gates OK)" (dead checkpoint, gates pass), or "FULL will be created (recovery gate failed: <reason>)" (dead checkpoint, gates fail). Dead-checkpoint predictions SHALL be preceded by the same WARNING the real run would log. Predictions SHALL NOT reference snapshot names and SHALL NOT predict per-snapshot transfer lists. Estimates are upper bounds and SHALL be presented as approximate.

#### Scenario: Gate open with healthy checkpoint predicts one delta per disk

- **WHEN** dry-run evaluates a disk with an existing checkpoint whose bitmap is HEALTHY and an open gate
- **THEN** exactly one prediction is emitted: delta since the newest checkpoint, with target and approximate size
- **AND** no NBD export, checkpoint, or file write occurs

#### Scenario: Gate closed predicts no backup

- **WHEN** the onchange gate is closed for a disk
- **THEN** no backup prediction is emitted for that disk

#### Scenario: No checkpoint predicts FULL

- **WHEN** no checkpoint exists for the disk and the gate is open
- **THEN** the prediction is "FULL will be created" regardless of snapshot state

#### Scenario: Dead checkpoint with passing gates predicts recovered delta

- **WHEN** the newest checkpoint's bitmap is DEAD and gates G1-G3 pass
- **THEN** dry-run logs the crash WARNING and predicts a recovered-delta with the allocation-superset size estimate
- **AND** predicts the dead checkpoint removal after verification

#### Scenario: Dead checkpoint with failed gate predicts FULL with reason

- **WHEN** the newest checkpoint's bitmap is DEAD and a gate fails
- **THEN** dry-run predicts FULL and names the failed gate

### Requirement: FULL backup prediction with size estimate

In dry-run mode, when the FULL/delta decision determines a FULL would be created, Core SHALL log an INFO prediction containing the disk target, the transfer method, the VM running state, and an estimated standalone size computed read-only from the disk's `base_image` backing chain (`qemu-img info --force-share --backing-chain --output=json`) — a real FULL exports the live disk plus a near-zero fresh overlay, so the base chain is the correct estimate source for both running and stopped VMs. The chain-size estimation logic SHALL be shared with `Core.fork()` via a single helper. The same estimate SHALL feed the dry-run free-space gate so prediction and gate never disagree. Estimation probe failures SHALL NOT log above DEBUG. When the estimation command fails, the prediction SHALL still be emitted with size unknown.

#### Scenario: FULL prediction carries chain size estimate
- **WHEN** dry-run predicts a FULL for disk `vda` whose `base_image` backing chain sums to 1 GiB of `actual-size`
- **THEN** the prediction log includes the disk, method, VM state, and an approximate size of 1 GiB

#### Scenario: Estimation failure degrades gracefully
- **WHEN** the `qemu-img info --backing-chain` call fails during dry-run
- **THEN** the FULL prediction is still logged, with the size marked unknown
- **AND** the pipeline does not abort

#### Scenario: Estimation never uses snapshot files

- **WHEN** dry-run estimates a FULL size
- **THEN** the estimate source is the disk's `base_image` backing chain
- **AND** no snapshot file path participates in the estimation

### Requirement: Backup retention prediction includes predicted FULLs

In dry-run mode, when a new FULL is predicted for a disk on a target, `Core._evaluate_backup_retention()` SHALL include the predicted FULL as an additional chain (timestamp = current time) in the generation count. Backup deletions that become eligible only because of the predicted FULL SHALL be predicted with an explicit condition that they execute only after the new FULL passes verification (verify-before-delete gate). In non-dry-run mode behavior SHALL be unchanged.

#### Scenario: Generation rollover predicted
- **WHEN** a target has 1 existing FULL generation, `target_keep_generations = 1`, and dry-run predicts a new FULL for disk `vda`
- **THEN** the old generation's backups appear in the deletion prediction
- **AND** each such deletion is marked conditional on new-FULL verification

### Requirement: Per-disk blockcommit prediction

In dry-run mode, `Core._blockcommit_snapshots()` SHALL log the predicted merges grouped by disk target, listing the snapshot names per disk, instead of a per-VM counter. One prediction entry per disk SHALL be recorded.

#### Scenario: Two disks produce two per-disk predictions
- **WHEN** the retention remove set contains 2 snapshots of `vda` and 1 snapshot of `vdb`
- **THEN** dry-run logs one blockcommit prediction for `vda` naming both snapshots and one for `vdb` naming its snapshot
- **AND** no `virsh blockcommit` or `qemu-img commit` command is executed

### Requirement: Hysteresis retention prediction in dry-run

In dry-run mode, the hysteresis retention evaluation SHALL run with exactly the same decision logic as a real run (mode selection, persisted collapse phase, trigger threshold, floor, and per-run cap) but produce predictions instead of mutations:

- Grow phase (`N <= snapshot_chain_length` and no persisted collapse phase for the disk): the retention remove set is empty, no blockcommit prediction is recorded, and Core MAY log an informational note that the chain is within the hysteresis band.
- Triggered or continuing collapse (`N > snapshot_chain_length`, or the collapse phase is persisted for the disk while `N > snapshot_preserve_min`): Core SHALL record one prediction entry per disk naming exactly the snapshots that would be merged — the oldest `min(N − snapshot_preserve_min, max_commits_per_run)` entries when the cap applies, otherwise the oldest `N − snapshot_preserve_min` — consistent with the existing "Per-disk blockcommit prediction" requirement. No lifecycle manager call is executed.
- The `collapse_in_progress` phase key SHALL be read for decision-making but SHALL NOT be set, extended, or cleared by a dry-run; the zero-mutation invariant applies to it as to every other state key.

#### Scenario: Grow phase predicts no commits
- **WHEN** `qsnap -n run` executes for a VM with `snapshot_retention_mode = "hysteresis"`, `snapshot_chain_length = 72`, `snapshot_preserve_min = 24`, and 60 snapshots in state
- **THEN** no blockcommit prediction is recorded for the disk
- **AND** no `virsh blockcommit` command is executed
- **AND** the state file is byte-identical after the run

#### Scenario: Collapse prediction is capped and names the oldest snapshots
- **WHEN** `qsnap -n run` executes for a hysteresis VM with 73 snapshots, floor 24, and `max_commits_per_run = 12`
- **THEN** one per-disk prediction is recorded naming the 12 oldest snapshots as merge candidates
- **AND** the newest 24 snapshots never appear in any prediction
- **AND** no lifecycle manager `blockcommit()` is called

#### Scenario: Persisted collapse phase drives prediction below the trigger threshold
- **WHEN** `qsnap -n run` executes while `collapse_in_progress` contains the disk and the state holds 60 snapshots (below the trigger 72, above the floor 24)
- **THEN** a per-disk prediction naming the oldest `60 − 24 = 36` snapshots capped by `max_commits_per_run` is recorded
- **AND** the `collapse_in_progress` key remains set and byte-identical after the run

### Requirement: Deferred drain prediction without mutation

In dry-run mode, `Core._check_deferred_operations()` SHALL NOT execute any blockcommit, SHALL NOT remove or re-queue deferred entries, SHALL NOT remove snapshots from state, and SHALL NOT refresh domain XML. For each queued entry Core SHALL compute the read-only commit plan (`_plan_blockcommit()`, which may call `virsh domstate`) and log a per-disk prediction of what would be drained, including the deferral split when the plan produces one. If the VM state cannot be determined, the prediction SHALL state that a drain would be attempted with the VM state unknown.

#### Scenario: Deferred queue survives dry-run byte-identical
- **WHEN** `qsnap -n run` is executed with a queued deferred blockcommit for disk `vda`
- **THEN** the deferred queue in state is unchanged after the run
- **AND** no lifecycle manager `blockcommit()` is called
- **AND** a per-disk prediction naming the queued snapshots is logged

### Requirement: Zero-mutation invariant for the dry-run pipeline

A dry-run pipeline execution (`run`, `snapshot`, `backup`, `prune`) SHALL NOT create, modify, or delete any file in snapshot directories or on backup targets, SHALL NOT write to `IStateManager`, SHALL NOT write a transaction log, and SHALL NOT modify domain XML, libvirt checkpoints, or backup jobs. State-hygiene self-healing (phantom FULL removal with dependency cascade, stale `last_backup_allocation` baseline cleanup, orphan and dead-bitmap checkpoint removal) SHALL be predicted with `[dry-run] Would ...` logs instead of being executed; the in-memory detection that drives downstream decisions (e.g. filtering phantom FULLs out of the FULL decision input) remains active because it performs no state write. Read-only shell commands are permitted: `qemu-img info`, `virsh domstate` / `virsh dominfo` / `virsh domblklist` / `virsh dumpxml` / `virsh checkpoint-list` / `virsh blockjob` / `virsh --version`, read-only `virsh qemu-monitor-command` introspection queries (`query-named-block-nodes`), `test`, `which`, `find`, `du`, and read-only listing commands. Every shell call issued during a dry-run SHALL be read-only.

#### Scenario: State and filesystem unchanged after dry-run
- **WHEN** `qsnap -n run` completes for a VM
- **THEN** the full `IStateManager` content for that VM is identical to the pre-run content
- **AND** no new file exists in the VM's snapshot directories or on its targets
- **AND** no transaction log line was written
- **AND** the set of libvirt checkpoints is unchanged

#### Scenario: Dry-run with phantom FULL records predicts cleanup without state writes
- **WHEN** `qsnap -n run` executes while state holds a FULL backup record whose file no longer exists on disk (with incremental dependency records attached)
- **THEN** dry-run logs a `[dry-run] Would remove phantom FULL ...` prediction including the dependency cascade count obtained read-only
- **AND** the phantom FULL record and its dependency records remain in `IStateManager` after the run
- **AND** any stale baseline cleanup that would follow is likewise logged, not executed

#### Scenario: Dry-run with stale baseline and no FULLs predicts baseline cleanup
- **WHEN** `qsnap -n run` executes while state holds a `last_backup_allocation` baseline for a target disk but no FULL backup records for that target
- **THEN** dry-run logs a `[dry-run] Would clear stale last_backup_allocation ...` prediction for that target and disk
- **AND** the baseline remains in `IStateManager` after the run

#### Scenario: Dry-run checkpoint probes are read-only
- **WHEN** dry-run executes the bitmap health probe and the blockjob probe
- **THEN** only read-only commands (`virsh qemu-monitor-command` introspection, `virsh blockjob`, `qemu-img info`) are issued
- **AND** no `checkpoint-create`, `checkpoint-delete`, `backup-begin`, or `domjobabort` is executed

### Requirement: Structured predictions channel

In dry-run mode, Core SHALL accumulate one prediction record per predicted mutation in a predictions list carried by `PipelineResult.predictions` (field defined in capability `action-audit-trail`). Prediction records SHALL use the `ActionRecord` structure with VM and disk context. In non-dry-run mode `PipelineResult.predictions` SHALL be empty. Predictions SHALL never be written to the transaction log.

#### Scenario: Dry-run populates predictions per disk
- **WHEN** `qsnap -n run` completes for a two-disk VM that would create 2 snapshots, merge 1 snapshot, create 1 FULL, and transfer 1 incremental
- **THEN** `result.predictions` contains records for each predicted action with correct `vm_name` and `disk`
- **AND** `result.actions` is empty

#### Scenario: Real run leaves predictions empty
- **WHEN** `qsnap run` completes with dry-run disabled
- **THEN** `result.predictions` is empty and `result.actions` is populated as before

### Requirement: Preflight cleanup is log-only in dry-run

In dry-run mode, `Core._preflight_cleanup()` SHALL NOT delete any file. Each of the three cleanup sites — stale `*.tmp`/`*.partial` files in snapshot directories and target directories, stale NBD sockets (`qsnap-backup-*.sock` under `/tmp`), and truncated non-FULL `.qcow2` files on backup targets (failed `qemu-img info`) — SHALL log `[dry-run] Would remove stale file: <path>`, `[dry-run] Would remove stale socket: <path>`, or `[dry-run] Would remove stale partial transfer: <path>` respectively, instead of running `rm -f`. `removed_count` SHALL NOT be incremented in dry-run. The read-only detection commands (`find`, `qemu-img info`) SHALL still run. By decision, these cleanups produce logs only — no prediction records.

#### Scenario: Stale tmp files predicted, not removed
- **WHEN** `qsnap -n run` executes with stale `*.tmp` files present in a snapshot directory
- **THEN** `[dry-run] Would remove stale file: <path>` is logged for each file
- **AND** no `rm` command is issued
- **AND** the files still exist after the run

#### Scenario: Stale NBD sockets predicted, not removed
- **WHEN** `qsnap -n run` executes with a stale `qsnap-backup-*.sock` socket in `/tmp`
- **THEN** `[dry-run] Would remove stale socket: <path>` is logged
- **AND** the socket file still exists after the run

#### Scenario: Truncated qcow2 predicted, not removed
- **WHEN** `qsnap -n run` executes with a truncated non-FULL `.qcow2` on a target (qemu-img info fails)
- **THEN** `[dry-run] Would remove stale partial transfer: <path>` is logged as WARNING
- **AND** the file still exists after the run

#### Scenario: Real run still cleans
- **WHEN** `qsnap run` (not dry-run) executes with the same stale files
- **THEN** the files are removed via `rm -f` and `removed_count` is incremented

### Requirement: Deferred threshold warnings do not write state in dry-run

In dry-run mode, `Core._check_deferred_thresholds()` SHALL NOT call `IStateManager.update_deferred_warning()`. The threshold WARNING/CRITICAL logs SHALL still be emitted. The deferred queue entries (including `last_warned_at`) SHALL be byte-identical after the run.

#### Scenario: Threshold warning logged but not persisted
- **WHEN** `qsnap -n run` executes with a deferred entry older than the warning threshold
- **THEN** the WARNING log is emitted
- **AND** `update_deferred_warning()` is not called
- **AND** the deferred entry's `last_warned_at` is unchanged after the run

### Requirement: Stale state entry healing is log-only in dry-run

In dry-run mode, the stale-state self-healing in `Core._blockcommit_snapshots()` (snapshot entries whose file no longer exists on disk) SHALL log `[dry-run] Would remove stale state entry: snapshot <name> file not found on disk` and exclude the entry from `to_merge`, but SHALL NOT call `IStateManager.remove_snapshot()`. The state entry SHALL remain after the run.

#### Scenario: Stale entry predicted, not removed from state
- **WHEN** `qsnap -n run` executes with a state snapshot entry whose file is missing on disk
- **THEN** `[dry-run] Would remove stale state entry: snapshot <name> ...` is logged
- **AND** `remove_snapshot()` is not called
- **AND** the entry remains in `IStateManager` after the run

### Requirement: Deep check does not write the last-check timestamp in dry-run

In dry-run mode, `Core.check(deep=True)` SHALL NOT call `_set_last_deep_check_time()` — no `_last_deep_check` state file is created or modified. The deep check itself (read-only `qemu-img check`) SHALL still run and report.

#### Scenario: Dry-run deep check leaves timestamp untouched
- **WHEN** `qsnap -n check --deep` executes
- **THEN** the deep check runs and reports results
- **AND** no `_last_deep_check` timestamp file is created or updated

