# Size Estimation

## Purpose

Runtime factual reporting of backup-related disk metrics. The previous size estimation formula (`base_size × 0.3`) was removed because it cannot predict data compressibility and always produced misleading projections. Now the system logs only factual data: base image size and compression type.

## Requirements

### Requirement: qsnap estimate CLI command

The system SHALL provide a `qsnap estimate [vm]` CLI subcommand that prints factual backup information without executing any pipeline actions. It SHALL accept an optional VM name filter positional argument. The output SHALL include: VM name, base image path, base image actual-size (from `qemu-img info`), compression type (from config), and compression enabled/disabled. The output SHALL NOT include projected FULL size, projected total size, estimated delta, or any computed projections.

#### Scenario: Estimate for specific VM
- **WHEN** `qsnap estimate myvm` is executed
- **THEN** a factual summary is printed to stdout for that VM only
- **AND** the summary includes: base image path, base image actual-size, compression type, compression enabled
- **AND** no projected sizes or deltas are printed

#### Scenario: Estimate for all VMs
- **WHEN** `qsnap estimate` is executed without a VM argument
- **THEN** factual summaries for all configured VMs are printed
- **AND** no projected sizes or deltas are printed
