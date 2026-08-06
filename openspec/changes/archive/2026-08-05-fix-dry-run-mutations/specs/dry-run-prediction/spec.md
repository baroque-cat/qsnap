## ADDED Requirements

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
