# Stall Detection (delta spec)

## ADDED Requirements

### Requirement: In-process stall watchdog for in-process transfers

When a data transfer executes as an in-process loop rather than a subprocess
(currently: the bitmap dirty-block copy loop), stall detection SHALL be
implemented as a progress watchdog inside that loop: a monotonic timestamp
updated after every successful chunk write; if no chunk completes for
`stall_timeout` seconds, the loop SHALL abort and the transfer SHALL return
an error string identical to the shell-level contract —
`"Stall detected: no progress for {N}s"`. When `stall_timeout` is 0, the
watchdog SHALL be disabled. The watchdog SHALL NOT spawn threads and SHALL
NOT log speed or progress — only the stall event. `IShell.run_with_stall_detection`
remains the mechanism for subprocess-based transfers (`qemu-img convert`
FULL exports, `rsync`) and is unchanged by this requirement.

#### Scenario: Watchdog aborts stalled copy loop

- **WHEN** the bitmap copy loop makes no progress for `stall_timeout` seconds
- **THEN** the loop aborts and the transfer returns
  `error="Stall detected: no progress for {N}s"`
- **AND** Core retry classification handles it exactly like a shell-level stall

#### Scenario: Watchdog disabled at zero timeout

- **WHEN** `stall_timeout` is 0
- **THEN** no watchdog check runs and the loop relies on NBD-level errors only

#### Scenario: Subprocess transfers unchanged

- **WHEN** a FULL backup runs `qemu-img convert`
- **THEN** it still uses `IShell.run_with_stall_detection` with output-file
  growth polling
