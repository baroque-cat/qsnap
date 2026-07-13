## ADDED Requirements

### Requirement: Systemd service unit
The system SHALL ship a `qsnap.service` systemd service unit that executes `qsnap run` with a configurable config file path. The service SHALL be of type `oneshot`.

#### Scenario: Service runs qsnap
- **WHEN** `systemctl start qsnap.service` is executed
- **THEN** `qsnap -c /etc/qsnap/qsnap.toml run` is executed and the service reports success or failure based on exit code

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
The system SHALL ship an example TOML configuration file with comments explaining each option.

#### Scenario: Example config is parseable
- **WHEN** `qsnap -c /usr/share/doc/qsnap/qsnap.toml.example list config` is executed
- **THEN** the config is parsed successfully and shows the documented VM definitions
