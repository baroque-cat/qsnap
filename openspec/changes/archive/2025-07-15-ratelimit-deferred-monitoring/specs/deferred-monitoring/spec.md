## ADDED Requirements

### Requirement: Deferred threshold config fields

The system SHALL support optional threshold fields on `GlobalConfig` for deferred blockcommit monitoring: `deferred_warn_count` (default `5`), `deferred_crit_count` (default `10`), `deferred_warn_age` (default `"7d"`), `deferred_crit_age` (default `"14d"`). All four fields SHALL be of type `str`.

#### Scenario: All deferred thresholds have defaults

- **WHEN** config file has no deferred threshold fields
- **THEN** `GlobalConfig.deferred_warn_count` is `"5"`, `deferred_crit_count` is `"10"`, `deferred_warn_age` is `"7d"`, `deferred_crit_age` is `"14d"`

#### Scenario: Deferred thresholds can be overridden

- **WHEN** config file has `deferred_warn_count = "3"` and `deferred_crit_age = "30d"`
- **THEN** those values are reflected in `GlobalConfig`

### Requirement: GlobalConfig immutability includes deferred thresholds

The deferred threshold fields SHALL be frozen (immutable) on `GlobalConfig`.

#### Scenario: Attempted mutation raises FrozenInstanceError

- **WHEN** a GlobalConfig is created with `deferred_warn_count="5"`
- **THEN** attempting to set `cfg.deferred_warn_count = "3"` raises `FrozenInstanceError`

### Requirement: Post-pipeline deferred threshold check

At the end of every `Core.run()`, `Core.snapshot()`, and `Core.backup()` invocation, the system SHALL check accumulated deferred blockcommit operations against the configured thresholds and log WARNING or CRITICAL messages. The check SHALL NOT affect the pipeline exit code.

#### Scenario: Deferred count below warn threshold — silent

- **WHEN** a VM has 2 deferred operations and `deferred_warn_count = "5"`
- **THEN** no WARNING or CRITICAL is logged for that VM

#### Scenario: Deferred count meets WARNING threshold

- **WHEN** a VM has 5 deferred operations and `deferred_warn_count = "5"`
- **THEN** a WARNING is logged: "VM <name>: 5 deferred blockcommit operations pending"

#### Scenario: Deferred count meets CRITICAL threshold

- **WHEN** a VM has 10 deferred operations and `deferred_crit_count = "10"`
- **THEN** a CRITICAL is logged: "VM <name>: 10 deferred blockcommit operations pending (CRITICAL)"

#### Scenario: Deferred age meets WARNING threshold

- **WHEN** a VM has 1 deferred operation aged 8 days and `deferred_warn_age = "7d"`
- **THEN** a WARNING is logged

#### Scenario: Deferred age meets CRITICAL threshold

- **WHEN** a VM has 1 deferred operation aged 15 days and `deferred_crit_age = "14d"`
- **THEN** a CRITICAL is logged

#### Scenario: Threshold check does not change exit code

- **WHEN** a deferred CRITICAL threshold is breached during `run()`
- **THEN** the pipeline exit code remains 0 (success), not 1 or 10

### Requirement: CLI `list deferred` command

The system SHALL provide a `qsnap list deferred` command that displays a table of deferred blockcommit operations across all VMs (or filtered by optional VM name arguments). Columns SHALL be: VM name (VM), number of pending snapshots (SNAPSHOTS), reason (REASON), and age of oldest deferred entry (AGE).

#### Scenario: List all deferred operations

- **WHEN** `qsnap list deferred` is run with two VMs having deferred operations
- **THEN** a table is displayed with rows for both VMs, sorted by age descending (oldest first)

#### Scenario: List deferred filtered by VM name

- **WHEN** `qsnap list deferred vm-home` is run
- **THEN** only "vm-home" rows are displayed

#### Scenario: List deferred with no deferred operations

- **WHEN** `qsnap list deferred` is run and no VM has any deferred operations
- **THEN** a message "No deferred blockcommit operations" is displayed

#### Scenario: List deferred with --format raw

- **WHEN** `qsnap list deferred --format raw` is run
- **THEN** output is in `key=value` space-separated format: `vm_name=vm-home snapshots=3 reason=apparmor since=2025-07-13T08:00:00`

### Requirement: DeferredBlockcommit gains last_warned_at field

The `DeferredBlockcommit` state record SHALL include an optional `last_warned_at` field of type `datetime | None`, defaulting to `None`. `IStateManager` implementations SHALL persist and restore this field.

#### Scenario: DeferredBlockcommit defaults last_warned_at to None

- **WHEN** a new `DeferredBlockcommit` is created without a `last_warned_at` argument
- **THEN** `last_warned_at` is `None`

#### Scenario: DeferredBlockcommit with explicit last_warned_at

- **WHEN** a `DeferredBlockcommit` is created with `last_warned_at=datetime(2025, 7, 13)`
- **THEN** `last_warned_at` is `datetime(2025, 7, 13)`

#### Scenario: State file round-trips last_warned_at

- **WHEN** a deferred blockcommit with `last_warned_at` is written to state and read back
- **THEN** `last_warned_at` has the same value

#### Scenario: Old state file without last_warned_at is backward-compatible

- **WHEN** a state JSON file lacks `last_warned_at` in a deferred entry
- **THEN** `_dict_to_deferred()` constructs a `DeferredBlockcommit` with `last_warned_at=None`

### Requirement: Remediation guidance in qsnap check

`Core.check()` SHALL include deferred operation status for each VM. When deferred operations are present, the output SHALL include actionable remediation guidance specific to the denial reason (apparmor or selinux).

#### Scenario: Check shows deferred with apparmor remediation

- **WHEN** `qsnap check` shows a VM with deferred operations caused by apparmor
- **THEN** the output includes: "Merge blocked by AppArmor. Consider: aa-disable /etc/apparmor.d/libvirt/libvirt-<uuid>" and "Or: shut down the VM to allow automatic merge."

#### Scenario: Check shows deferred with selinux remediation

- **WHEN** `qsnap check` shows a VM with deferred operations caused by selinux
- **THEN** the output includes: "Merge blocked by SELinux." with remediation suggestions

#### Scenario: Check shows healthy VM with no remediation

- **WHEN** `qsnap check` shows a VM with zero deferred operations
- **THEN** no remediation guidance is displayed for that VM

### Requirement: Deferred severity levels

The system SHALL classify deferred status into severity levels based on count and age thresholds: OK (below all thresholds), WARNING (either count or age meets warn threshold), CRITICAL (either count or age meets crit threshold).

#### Scenario: OK status when below all thresholds

- **WHEN** a VM has 2 deferred operations aged 3 days, with all thresholds at their defaults (warn_count=5, warn_age=7d)
- **THEN** deferred status is OK

#### Scenario: WARNING status when count meets threshold

- **WHEN** a VM has 5 deferred operations aged 1 day, with warn_count=5
- **THEN** deferred status is WARNING

#### Scenario: CRITICAL status when age meets threshold

- **WHEN** a VM has 3 deferred operations aged 15 days, with crit_age=14d
- **THEN** deferred status is CRITICAL
