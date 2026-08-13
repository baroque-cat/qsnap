# Hysteresis Retention

## Purpose

Grow-to-threshold / collapse-to-floor snapshot retention for the snapshot world. Hysteresis is the default retention mode: the backing chain grows with no blockcommits while the snapshot count `N ≤ H` (the trigger threshold, `snapshot_chain_length`); once `N > H` the oldest `N − L` snapshots are merged down to the floor `L` (`snapshot_preserve_min`) as a single uncapped bulk blockcommit within the same run. No collapse phase is persisted and no per-run commit cap exists: a deferred or failed collapse re-triggers naturally on the next run because `N > H` still holds. The older steady count-based mode remains available via `snapshot_retention_mode = "steady"`. Target/backup retention is unaffected.

## Requirements

### Requirement: Hysteresis retention mode selection

The system SHALL support a per-VM snapshot retention mode selected by `snapshot_retention_mode` with values `"steady"` and `"hysteresis"` (default `"hysteresis"`, inherited global → VM). In `"steady"` mode retention behavior is exactly the existing count-based policy (keep the newest `snapshot_chain_length`, commit the excess every run). In `"hysteresis"` mode `snapshot_chain_length` SHALL be interpreted as the trigger threshold **H** and `snapshot_preserve_min` as the collapse floor **L**. The mode SHALL affect only snapshot-world retention; target/backup retention is unaffected.

#### Scenario: Default mode is hysteresis

- **WHEN** `snapshot_retention_mode` is not configured
- **THEN** retention behaves as hysteresis: grow to threshold H with no commits, then collapse to floor L in a single run

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
