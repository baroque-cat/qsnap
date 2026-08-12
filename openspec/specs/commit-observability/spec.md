# Commit Observability

## Purpose

Operator-actionable log lines for every commit attempt, live-commit heartbeat, and
reconciliation/recovery outcome, so progress is visible and deferred or aborted commits can
be diagnosed.

## Requirements

### Requirement: Commit intent log before the manager call

Immediately before invoking the lifecycle manager for a disk, Core SHALL log an INFO line
naming the VM, disk, number of snapshots in the merge set, the base image, the effective
executor mode (`virsh` or `qemu-img`), and the configured timeout in seconds, e.g.
`[blockcommit] {vm}/{disk}: committing {n} snapshot(s) into {base} (mode={mode},
timeout={timeout}s)`. This line SHALL be emitted for both the main pipeline commit path and
the deferred-queue drain path.

#### Scenario: Intent line precedes every commit attempt

- **WHEN** Core commits merge set `["s1"]` for `vm1/vda` in mode `virsh` with timeout 1800
- **THEN** an INFO log line matching `[blockcommit] vm1/vda: committing 1 snapshot(s)` with `mode=virsh` and `timeout=1800s` appears before the `virsh blockcommit` command is spawned

#### Scenario: Drain path also logs intent

- **WHEN** the deferred-queue drain commits snapshots for a disk
- **THEN** the same intent INFO line is emitted before execution

### Requirement: Heartbeat during live commit waits

`BlockCommitManager` SHALL execute `virsh blockcommit --wait` through
`IShell.run_with_heartbeat` with a heartbeat interval of 60 seconds. On every heartbeat the
manager SHALL log an INFO line naming the VM, disk, the snapshot being merged, and the
elapsed seconds, e.g. `[blockcommit] {vm}/{disk}: still merging {snapshot} into base
({elapsed}s elapsed)`. A live commit therefore produces at most 60 seconds of log silence.

#### Scenario: Heartbeat lines appear during a long commit

- **WHEN** a live blockcommit runs for 150 seconds
- **THEN** at least two heartbeat INFO lines are logged (at ~60s and ~120s), each naming the VM, disk, and snapshot

#### Scenario: Fast commit produces no heartbeat

- **WHEN** a live blockcommit completes in under 60 seconds
- **THEN** no heartbeat line is emitted and the result is logged normally

### Requirement: Reconciliation and recovery outcomes are logged

Every reconciliation and intent-recovery outcome SHALL produce a log line that an operator
can act on:

- `late_success` → WARNING naming VM, disk, and merged snapshots, stating the commit
  completed after a client timeout and state was synced.
- `job_active` → WARNING naming VM, disk, and the detected job; states the disk was deferred.
- `failure` → ERROR with the original timeout error plus the hint to inspect `virsh blockjob`
  and the libvirtd journal.
- `inconclusive` → WARNING stating reality could not be determined and the disk was deferred
  fail-closed.
- Stale intent cleared with no effect → WARNING stating the previous run died during a commit
  attempt and no effect was observed.

#### Scenario: Each outcome is distinguishable in the log

- **WHEN** reconciliation ends in any of the five outcomes above
- **THEN** exactly one corresponding WARNING/ERROR line is emitted containing the VM name and disk
