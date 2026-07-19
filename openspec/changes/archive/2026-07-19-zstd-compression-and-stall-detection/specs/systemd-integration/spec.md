## MODIFIED Requirements

### Requirement: qsnap.service unit file

The system SHALL provide a `qsnap.service` systemd unit file with `Type=oneshot`. The unit file SHALL include `TimeoutStartSec=0` to disable systemd's default oneshot timeout (90 seconds by default). This allows qsnap's internal stall detection (`backup_stall_timeout`) to be the sole authority for transfer timeout enforcement. Without `TimeoutStartSec=0`, systemd would kill the service after 90 seconds, defeating stall detection for any backup that takes longer than 90 seconds.

#### Scenario: qsnap.service has TimeoutStartSec=0
- **WHEN** the qsnap.service unit file is generated
- **THEN** it contains `TimeoutStartSec=0`
- **AND** systemd does not kill the service based on elapsed time

#### Scenario: qsnap.service is Type=oneshot
- **WHEN** the qsnap.service unit file is generated
- **THEN** it contains `Type=oneshot`
- **AND** systemd waits for the process to complete before considering the service started

#### Scenario: qsnap.timer triggers qsnap.service
- **WHEN** the qsnap.timer fires
- **THEN** systemd starts qsnap.service
- **AND** the service runs until qsnap exits (no systemd timeout kill)
- **AND** qsnap's internal stall detection handles hung transfers
