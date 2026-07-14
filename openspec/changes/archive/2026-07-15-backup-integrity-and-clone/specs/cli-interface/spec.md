## ADDED Requirements

### Requirement: CLI supports --print-schedule flag

The CLI SHALL accept a `--print-schedule` (short: `-S`) flag that invokes `Core.schedule_summary()` and prints the result to stdout.

#### Scenario: --print-schedule with qsnap run
- **WHEN** `qsnap run --print-schedule --dry-run` is executed
- **THEN** the schedule summary is printed before the dry-run pipeline output

#### Scenario: Standalone --print-schedule
- **WHEN** `qsnap snapshot --print-schedule` is executed
- **THEN** the schedule summary is printed to stdout and exits without creating snapshots

### Requirement: Schedule summary logged at INFO during timer invocation

When `qsnap run` is invoked via systemd timer (detectable via `--timer` flag or environment variable), the schedule summary output SHALL be logged at INFO level alongside the pipeline starting message.

#### Scenario: Timer invocation logs summary
- **WHEN** `qsnap run --timer` is executed
- **THEN** the schedule summary output appears in the log at INFO level
