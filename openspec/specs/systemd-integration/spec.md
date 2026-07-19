## Requirements

### Requirement: Systemd service unit
The system SHALL ship a `qsnap.service` systemd service unit that executes `qsnap run` with a configurable config file path. The service SHALL be of type `oneshot`. The unit file SHALL include `TimeoutStartSec=0` to disable systemd's default oneshot timeout (90 seconds by default). This allows qsnap's internal stall detection (`backup_stall_timeout`) to be the sole authority for transfer timeout enforcement. Without `TimeoutStartSec=0`, systemd would kill the service after 90 seconds, defeating stall detection for any backup that takes longer than 90 seconds.

#### Scenario: Service runs qsnap
- **WHEN** `systemctl start qsnap.service` is executed
- **THEN** `qsnap -c /etc/qsnap/qsnap.toml run` is executed and the service reports success or failure based on exit code

#### Scenario: qsnap.service has TimeoutStartSec=0
- **WHEN** the qsnap.service unit file is generated
- **THEN** it contains `TimeoutStartSec=0`
- **AND** systemd does not kill the service based on elapsed time

#### Scenario: qsnap.service is Type=oneshot
- **WHEN** the qsnap.service unit file is generated
- **THEN** it contains `Type=oneshot`
- **AND** systemd waits for the process to complete before considering the service started

### Requirement: Systemd timer unit
The system SHALL ship a `qsnap.timer` systemd timer unit that triggers `qsnap.service` on a schedule. Default schedule SHALL be hourly with `Persistent=True` and `RandomizedDelaySec=300`.

#### Scenario: Timer triggers service
- **WHEN** the timer fires at the scheduled time
- **THEN** `qsnap.service` is started

#### Scenario: Persistent timer catches up after sleep
- **WHEN** the system was powered off during a scheduled run
- **THEN** `Persistent=True` causes the timer to fire immediately on next boot

### Requirement: Multiple timer instances with different configs
System administrators SHALL be able to create multiple timer/service pairs pointing to different config files for different backup cadences (e.g., hourly snapshots vs. weekly backups).

#### Scenario: Separate hourly and weekly timers
- **WHEN** `qsnap-hourly.timer` and `qsnap-weekly.timer` are both enabled
- **THEN** `qsnap-hourly.service` runs with one config, `qsnap-weekly.service` runs with a different config, without conflict

### Requirement: Example config file
The system SHALL ship an example TOML configuration file with comments explaining each option, including all fault-tolerance and safety fields.

#### Scenario: Example config is parseable
- **WHEN** `qsnap -c /usr/share/doc/qsnap/qsnap.toml.example list config` is executed
- **THEN** the config is parsed successfully and shows the documented VM definitions

#### Scenario: Example config documents preserve_min fields
- **WHEN** the example config is read
- **THEN** `snapshot_preserve_min` and `target_preserve_min` are documented with usage examples

#### Scenario: Example config documents all safety fields
- **WHEN** the example config is read
- **THEN** `auto_cleanup`, `state_backup_count`, `chain_verify_before_commit`, `chain_verify_after_commit`, `deep_check_schedule`, `blockcommit_deep_verify`, `snapshot_deep_verify`, `backup_retry_max`, and `backup_retry_base` are all documented

### Requirement: Deep verification systemd timer and service
The system SHALL ship a `qsnap-check.timer` systemd timer unit that triggers `qsnap-check.service` on a weekly schedule (Sunday at 03:00), with `Persistent=True` and `RandomizedDelaySec=1800`. The service SHALL execute `qsnap -c /etc/qsnap/qsnap.toml check --deep`. See `specs/deep-verification-circuit/spec.md`.

#### Scenario: Deep check timer ships with correct defaults
- **WHEN** the qsnap package is installed
- **THEN** `qsnap-check.timer` and `qsnap-check.service` are present in the systemd unit directory
- **AND** the timer is NOT enabled by default (operator must enable explicitly)

#### Scenario: Enabling the deep check timer
- **WHEN** `systemctl enable --now qsnap-check.timer` is executed
- **THEN** deep checks run weekly at the configured time
