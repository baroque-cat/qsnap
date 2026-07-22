## MODIFIED Requirements

### Requirement: run_with_stall_detection

`IShell.run_with_stall_detection(cmd, output_file, stall_timeout, check)` SHALL execute *cmd* with output-growth monitoring. Used for long-running data-transfer commands. After the NBD transfer unification, the primary data path is `pread`/`pwrite` through `INbdClient` with an in-process stall watchdog (not `IShell.run_with_stall_detection`). The method survives for any remaining subprocess-based data-transfer needs (e.g., future offline backup via standalone `qemu-nbd`).

#### Scenario: Stall detection kills stalled process

- **WHEN** *output_file* shows no size growth for *stall_timeout* seconds
- **THEN** the process is killed

#### Scenario: Data flows to completion

- **WHEN** *output_file* grows steadily
- **THEN** the process runs to completion with no maximum timeout
