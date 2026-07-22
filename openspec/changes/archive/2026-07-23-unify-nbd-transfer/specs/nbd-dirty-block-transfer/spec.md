## MODIFIED Requirements

### Requirement: Incremental output is a backing-chained COW delta

The unified NBD transfer engine SHALL produce backing-chained qcow2 deltas for incremental transfers and standalone qcow2 files for FULL transfers. For incrementals, the delta SHALL be created via `qemu-img create -f qcow2 -b <previous_backup> -F qcow2 <target>.tmp` and served by a forked `qemu-nbd`. For FULLs, the target SHALL be created via `qemu-img create -f qcow2 [-o compression_type=zstd] <target>.tmp` (standalone, no backing). The same `pread`/`pwrite` engine transfers data in both cases — the only difference is meta-contexts (`base:allocation` only for FULL; `base:allocation` + `qemu:dirty-bitmap` for incremental), extent filtering (allocated only for FULL; dirty∩allocated for incremental), and `zero_skip` (True for FULL, False for incremental).

#### Scenario: qemu-img info shows the backing chain (incremental)

- **WHEN** an incremental transfer completes
- **THEN** `qemu-img info` on the delta shows `backing file: <previous_backup>.qcow2`
- **AND** the delta contains only dirty∩allocated blocks written via `pwrite`

#### Scenario: qemu-img info shows no backing file (FULL)

- **WHEN** a FULL transfer completes
- **THEN** `qemu-img info` on the target shows `backing file: <none>`
- **AND** the target contains all allocated blocks (zero blocks may be skipped via `zero_skip`)

#### Scenario: Restore resolves bitmap chains unchanged

- **WHEN** a backup chain (FULL + incrementals) is restored
- **THEN** the standard qcow2 backing-chain resolution produces the correct virtual disk content
- **AND** no special restore tool is needed (standard `qemu-img rebase -u` + chain copy)
