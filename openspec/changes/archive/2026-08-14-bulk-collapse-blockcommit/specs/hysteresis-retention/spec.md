## MODIFIED Requirements

### Requirement: Hysteresis retention mode selection

The system SHALL support a per-VM snapshot retention mode selected by `snapshot_retention_mode` with values `"steady"` and `"hysteresis"` (default `"hysteresis"`, inherited global → VM). In `"steady"` mode retention behavior is exactly the existing count-based policy (keep the newest `snapshot_chain_length`, commit the excess every run). In `"hysteresis"` mode `snapshot_chain_length` SHALL be interpreted as the trigger threshold **H** and `snapshot_preserve_min` as the collapse floor **L**. The mode SHALL affect only snapshot-world retention; target/backup retention is unaffected.

#### Scenario: Default mode is hysteresis

- **WHEN** `snapshot_retention_mode` is not configured
- **THEN** retention behaves as hysteresis: grow to threshold H with no commits, then collapse to floor L in a single run

#### Scenario: Hysteresis mode reinterprets the knobs

- **WHEN** a VM has `snapshot_retention_mode = "hysteresis"`, `snapshot_chain_length = 72`, `snapshot_preserve_min = 24`
- **THEN** the trigger threshold H is 72 and the collapse floor L is 24

### Requirement: Grow phase below the trigger threshold

In hysteresis mode, WHILE the number of snapshots `N` for a disk satisfies `N ≤ H`, Core SHALL mark NO snapshots for commit for that disk. Snapshot creation continues normally each run. No phase state exists or is needed: the trigger condition alone fully determines behavior on every run.

#### Scenario: Chain at threshold does not commit

- **WHEN** hysteresis mode is active with `H = 72` and `N = 72` after snapshot creation
- **THEN** the remove set for the disk is empty
- **AND** no blockcommit command is issued for the disk

#### Scenario: Growth accumulates without commits

- **WHEN** over successive runs `N` grows from 30 to 72
- **THEN** no snapshot is committed on any of those runs

### Requirement: Collapse trigger and floor

In hysteresis mode, WHEN `N > H` for a disk, Core SHALL mark the oldest `N − L` snapshots of that disk for commit (subject to the oldest-prefix filter and the preserve-min floor trim, WITHOUT any per-run cap) and SHALL commit the entire marked set within the SAME run as a single bulk operation (capability `lifecycle-manager`). After a successful collapse the disk's snapshot count SHALL equal `L`. Snapshots newer than the floor (the newest `L`) SHALL never be marked by the collapse. If a collapse was deferred or failed, the unchanged condition `N > H` on the next run SHALL re-trigger an identical collapse attempt — no persisted phase is required.

#### Scenario: Trigger fires above threshold

- **WHEN** hysteresis mode is active with `H = 72`, `L = 24`, and `N = 73` after snapshot creation
- **THEN** the oldest 49 snapshots are marked for commit
- **AND** the newest 24 snapshots are kept
- **AND** all 49 are merged within the same run

#### Scenario: Floor snapshots are never committed

- **WHEN** a collapse marks snapshots for commit
- **THEN** the newest `L` snapshots of the disk are never included in the remove set

#### Scenario: Deferred collapse re-triggers naturally

- **WHEN** a collapse attempt was deferred (e.g. active foreign block job, MAC denial, ENOSPC) and the next run still observes `N > H`
- **THEN** the same oldest `N − L` set is marked again without any persisted phase marker

### Requirement: Hysteresis observability

Core SHALL emit operator-facing log lines for the collapse lifecycle: an INFO line when a collapse is initiated naming VM, disk, merge count, current snapshot count, and floor; and an INFO line when the collapse succeeds naming VM, disk, and the number of snapshots collapsed. Dry-run SHALL emit the equivalent predictions. No started/active/complete phase wording exists — a collapse is a single-run event.

#### Scenario: Trigger logs the collapse

- **WHEN** a collapse starts for `vm1/vda` merging 49 of 73 snapshots down to floor 24
- **THEN** an INFO line names `vm1`, `vda`, 49, 73, and 24

## REMOVED Requirements

### Requirement: Persisted collapse phase

**Reason**: The `collapse_in_progress` phase existed only because the per-run cap spread one logical collapse across multiple runs. With single-shot bulk collapse (no cap), a collapse either completes inside its run or is retried identically by the next run because `N > H` still holds; crash recovery is fully covered by the existing commit intent journal (`commit_in_progress`, capability `commit-intent-journal`).

**Migration**: None required. The `set/get/clear_collapse_in_progress` methods are removed from `IStateManager` (BREAKING for implementations and mocks). Stale `collapse_in_progress` keys left in `/var/lib/qsnap/state/{vm}.json` files are ignored by readers (unknown keys were always tolerated); nothing writes them anymore.

### Requirement: Per-run commit cap

**Reason**: `max_commits_per_run` portioned the collapse into 12-snapshot batches to bound run duration under the slow one-snapshot-per-job executor. The bulk segment commit merges the entire remove set in one `virsh blockcommit` job, so the cap has no remaining purpose; keeping it would silently reintroduce multi-run collapses.

**Migration**: Remove any `max_commits_per_run` line from `/etc/qsnap/qsnap.toml`. ConfigFacade SHALL reject the key with an actionable `ConfigError` (capability `config-model`). Run duration is now bounded by the scaled timeout budget (capability `core-orchestrator`), and lock contention from overlapping hourly runs resolves itself via exit code 3.
