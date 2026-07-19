## REMOVED Requirements

### Requirement: Core logs size estimation on every pipeline run

**Reason**: The `base_size × 0.3` compression factor formula cannot predict data compressibility. Real data (text, binaries, encrypted, already-compressed) has wildly different compression ratios (0.1–0.8). The estimate always produces misleading projections, providing false confidence or false alarm. The user explicitly requested removal: "it lies a lot and can't know what the data is, so it only makes things worse."

**Migration**: The pipeline step `_log_size_estimate()` (design D5) is removed from `Core._execute_pipeline()`. The `schedule_summary()` and `estimate()` methods are simplified to log only factual data: `base_size` (from `qemu-img info actual-size`) and `compression_type` (from config). No projected FULL size, projected total size, or estimated delta is computed. The `qsnap estimate` CLI command remains but outputs only factual data without projections.

### Requirement: Size estimation formula

**Reason**: The formula `num_fulls × full_size + num_incs × inc_size` where `full_size = base_size × 0.3` (when compression is enabled) is fundamentally flawed because the 0.3 factor is a hardcoded guess that cannot predict real-world data compressibility.

**Migration**: No replacement formula. The system logs `base_size` (factual) and `compression_type` (config) without computing projections. Users who need size estimates can run `qemu-img convert` on a test disk or check the actual size of previous FULL backups on the target.

## MODIFIED Requirements

### Requirement: qsnap estimate CLI command

The system SHALL provide a `qsnap estimate [vm]` CLI subcommand that prints factual backup information without executing any pipeline actions. It SHALL accept an optional VM name filter positional argument. The output SHALL include: VM name, base image path, base image actual-size (from `qemu-img info`), compression type (from config), and compression enabled/disabled. The output SHALL NOT include projected FULL size, projected total size, estimated delta, or any computed projections — these were removed because compression ratios cannot be predicted from data type alone.

#### Scenario: Estimate for specific VM
- **WHEN** `qsnap estimate myvm` is executed
- **THEN** a factual summary is printed to stdout for that VM only
- **AND** the summary includes: base image path, base image actual-size, compression type, compression enabled
- **AND** no projected sizes or deltas are printed

#### Scenario: Estimate for all VMs
- **WHEN** `qsnap estimate` is executed without a VM argument
- **THEN** factual summaries for all configured VMs are printed
- **AND** no projected sizes or deltas are printed
