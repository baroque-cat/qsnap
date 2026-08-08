# Dry-Run Prediction — delta

## MODIFIED Requirements

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
