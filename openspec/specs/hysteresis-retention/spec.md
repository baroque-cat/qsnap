# Hysteresis Retention

## Purpose

Grow-to-threshold / collapse-to-floor snapshot retention for the snapshot world. Hysteresis is the default retention mode: the backing chain grows with no blockcommits while the snapshot count `N ≤ H` (the trigger threshold, `snapshot_chain_length`); once `N > H` the oldest `N − L` snapshots are merged down to the floor `L` (`snapshot_preserve_min`), bounded per run by `max_commits_per_run`. The collapse is a persisted per-disk phase that continues across runs until `N ≤ L`, then the chain grows again. The older steady count-based mode remains available via `snapshot_retention_mode = "steady"`. Target/backup retention is unaffected.

## Requirements

### Requirement: Hysteresis retention mode selection

The system SHALL support a per-VM snapshot retention mode selected by `snapshot_retention_mode` with values `"steady"` and `"hysteresis"` (default `"hysteresis"`, inherited global → VM). In `"steady"` mode retention behavior is exactly the existing count-based policy (keep the newest `snapshot_chain_length`, commit the excess every run). In `"hysteresis"` mode `snapshot_chain_length` SHALL be interpreted as the trigger threshold **H** and `snapshot_preserve_min` as the collapse floor **L**. The mode SHALL affect only snapshot-world retention; target/backup retention is unaffected.

#### Scenario: Default mode is hysteresis

- **WHEN** `snapshot_retention_mode` is not configured
- **THEN** retention behaves as hysteresis: grow to threshold H with no commits, then collapse to floor L
- **AND** collapse-phase state is written only when a collapse triggers

#### Scenario: Hysteresis mode reinterprets the knobs

- **WHEN** a VM has `snapshot_retention_mode = "hysteresis"`, `snapshot_chain_length = 72`, `snapshot_preserve_min = 24`
- **THEN** the trigger threshold H is 72 and the collapse floor L is 24

### Requirement: Hysteresis validation

ConfigFacade SHALL reject a configuration where hysteresis mode is active for a VM and the resolved values violate `H > L` or `L ≥ 1`, raising `ConfigError` naming both resolved values. Validation SHALL apply after option inheritance (global → VM).

#### Scenario: Floor above threshold is rejected

- **WHEN** hysteresis mode is active with resolved `H = 24` and `L = 48`
- **THEN** config loading fails with a `ConfigError` mentioning both values

#### Scenario: Zero floor is rejected

- **WHEN** hysteresis mode is active with `L = 0`
- **THEN** config loading fails with a `ConfigError`

### Requirement: Grow phase below the trigger threshold

In hysteresis mode, WHILE the collapse phase is inactive AND the number of snapshots `N` for a disk satisfies `N ≤ H`, Core SHALL mark NO snapshots for commit for that disk. Snapshot creation continues normally each run.

#### Scenario: Chain at threshold does not commit

- **WHEN** hysteresis mode is active with `H = 72`, phase inactive, and `N = 72` after snapshot creation
- **THEN** the remove set for the disk is empty
- **AND** no blockcommit command is issued for the disk

#### Scenario: Growth accumulates without commits

- **WHEN** over successive runs `N` grows from 30 to 72
- **THEN** no snapshot is committed on any of those runs

### Requirement: Collapse trigger and floor

In hysteresis mode, WHEN `N > H` for a disk (or the collapse phase is already active with `N > L`), Core SHALL mark the oldest `N − L` snapshots of that disk for commit (subject to the oldest-prefix filter and the per-run cap). After the marked snapshots are committed and state converges, the disk's snapshot count SHALL move toward `L`. Snapshots newer than the floor (the newest `L`) SHALL never be marked by the collapse.

#### Scenario: Trigger fires above threshold

- **WHEN** hysteresis mode is active with `H = 72`, `L = 24`, phase inactive, and `N = 73` after snapshot creation
- **THEN** the oldest 49 snapshots are marked for commit (before cap truncation)
- **AND** the newest 24 snapshots are kept

#### Scenario: Floor snapshots are never committed

- **WHEN** a collapse marks snapshots for commit
- **THEN** the newest `L` snapshots of the disk are never included in the remove set

### Requirement: Persisted collapse phase

Core SHALL persist the collapse phase per disk in the VM state key `collapse_in_progress` (list of disk names; missing key = no phase). The phase SHALL be written BEFORE the blockcommit step of the triggering run starts, SHALL be kept while the disk's post-commit snapshot count `N > L`, and SHALL be cleared when the count reaches `N ≤ L` (either after successful commit convergence in the same run, or defensively when evaluation observes `N ≤ L` with the phase active). Dry-run SHALL predict phase transitions without writing them. Reset operations (`reset_vm_state`, `reset_vm_disk_state`) SHALL clear the marker for the affected scope.

#### Scenario: Phase survives a capped run

- **WHEN** the trigger fired with 50 snapshots to merge and the cap allowed 12 this run
- **THEN** `collapse_in_progress` contains the disk after the run
- **AND** the next run continues collapsing without requiring `N > H`

#### Scenario: Phase cleared at the floor

- **WHEN** a collapse run ends with the disk's snapshot count `≤ L`
- **THEN** the disk is removed from `collapse_in_progress`
- **AND** subsequent runs make no commits while `N ≤ H`

#### Scenario: Phase set before irreversible work

- **WHEN** the trigger fires during retention evaluation
- **THEN** the phase marker is persisted before any `virsh blockcommit` of the collapse is invoked

#### Scenario: Defensive clear after external shrink

- **WHEN** the phase is active but evaluation observes `N ≤ L` (e.g. after restore or healing)
- **THEN** the phase is cleared and no snapshots are marked

### Requirement: Per-run commit cap

The global option `max_commits_per_run` (integer ≥ 0, default 12, 0 = unlimited) SHALL cap how many snapshots one run may mark for commit per disk in BOTH retention modes. Truncation SHALL keep the OLDEST entries of the (already floor-trimmed, oldest-first) remove list. The cap SHALL NOT override the preserve-min floor invariant (at least the newest `L` snapshots remain uncommitted). The cap applies to the snapshot world only.

#### Scenario: Cap truncates a large collapse

- **WHEN** the remove set is 49 snapshots (oldest-first) and `max_commits_per_run = 12`
- **THEN** only the 12 oldest snapshots are committed this run
- **AND** the remaining 37 stay in state for subsequent runs

#### Scenario: Cap zero means unlimited

- **WHEN** `max_commits_per_run = 0` and the remove set is 49
- **THEN** all 49 snapshots are committed in the same run

#### Scenario: Cap never breaks the floor

- **WHEN** the cap truncates a remove set
- **THEN** the newest `L` snapshots of the disk remain uncommitted

### Requirement: Hysteresis observability

Core SHALL emit operator-facing log lines for the collapse lifecycle: an INFO line when the phase starts (naming VM, disk, merge count, current count, floor), an INFO line on each capped continuation run (merged this run, remaining above floor), and an INFO line when the floor is reached. Dry-run SHALL emit the equivalent predictions.

#### Scenario: Trigger logs the collapse start

- **WHEN** the collapse phase starts for `vm1/vda` with 49 of 73 snapshots down to floor 24
- **THEN** an INFO line names `vm1`, `vda`, 49, 73, and 24
