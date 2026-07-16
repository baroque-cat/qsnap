## ADDED Requirements

### Requirement: qsnap estimate subcommand
The CLI SHALL provide an `estimate` subcommand with an optional `VM` positional filter argument. It SHALL map to `Core.schedule_summary()` with size estimation enabled. It SHALL NOT execute any pipeline actions. Output SHALL include per-VM and per-target size projections.

#### Scenario: Estimate for specific VM
- **WHEN** `qsnap estimate myvm` is executed
- **THEN** a size projection is printed to stdout for that VM only
- **AND** no pipeline actions are executed

#### Scenario: Estimate for all VMs
- **WHEN** `qsnap estimate` is executed without a VM argument
- **THEN** size projections for all configured VMs are printed
- **AND** no pipeline actions are executed

#### Scenario: Estimate respects --format flag
- **WHEN** `qsnap estimate --format raw` is executed
- **THEN** output is in `key=value` format for machine consumption
