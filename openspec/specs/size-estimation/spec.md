# Size Estimation

## Purpose

Runtime factual reporting of backup-related disk metrics. Iterates per-disk for base image actual-size. The previous size estimation formula (`base_size × 0.3`) was removed because it cannot predict data compressibility. Now the system logs only factual data: per-disk base image size, compression type, and retention config.

## Requirements

### Requirement: qsnap estimate CLI command

The system SHALL provide a `qsnap estimate [vm]` CLI subcommand that prints factual backup information without executing any pipeline actions. It SHALL accept an optional VM name filter positional argument. The output SHALL iterate per-disk and include: `[disk.target] Current allocated: ~X.X GB` (from `qemu-img info actual-size`). For each target it SHALL show: `chain_length`, `keep_generations`, current chain count, compression type, and compression enabled/disabled. The output SHALL NOT include projected FULL size, projected total size, estimated delta, or any computed projections.

#### Scenario: Estimate shows per-disk size
- **WHEN** `qsnap estimate myvm` is executed on a VM with disks `vda` (20 GB) and `vdb` (50 GB)
- **THEN** output includes:
  ```
  === myvm ===
    [vda] Current allocated: ~20.0 GB
    [vdb] Current allocated: ~50.0 GB
    Backups [/backup/myvm]:
      chain_length: 0
      keep_generations: 1
      Current chains: 1
      Compression: zstd (compress=True)
  ```
- **AND** no projected sizes or deltas are printed

#### Scenario: Estimate for all VMs
- **WHEN** `qsnap estimate` is executed without a VM argument
- **THEN** per-disk factual summaries for all configured VMs are printed
- **AND** no projected sizes or deltas are printed
