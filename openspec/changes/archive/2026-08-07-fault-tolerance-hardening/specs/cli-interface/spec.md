# CLI Interface — Delta

## MODIFIED Requirements

### Requirement: Exit codes
The CLI SHALL return structured exit codes: 0 for success, 1 for generic error, 2 for
parse error, 3 for lockfile error, 4 for disk-full (run limited by one or more
space-classified errors), 10 for backup abort. Exit code 4 SHALL be returned when
`PipelineResult.space_limited` is `True` — i.e. any target was suspended by a reactive
ENOSPC, a proactive strict free-space gate, a blockcommit was deferred with reason
`enospc`, or a state write failed with ENOSPC. Precedence: parse error (2) and lockfile
(3) are evaluated before run results; disk-full (4) takes precedence over generic
failure (1); backup abort (10) applies to verification/non-space backup failures and is
evaluated alongside (4) — a run exhibiting both reports 4.

#### Scenario: Success exit code
- **WHEN** `qsnap run` completes with no errors
- **THEN** exit code is 0

#### Scenario: Lockfile error exit code
- **WHEN** `qsnap run` is executed and the lockfile is held by another process
- **THEN** exit code is 3, and a message is printed to stderr

#### Scenario: Disk-full exit code
- **WHEN** `qsnap run` completes with one target suspended by ENOSPC
- **THEN** exit code is 4
- **AND** the summary names the space-limited target

#### Scenario: Disk-full precedence over generic failure
- **WHEN** the result has `success=False` and `space_limited=True`
- **THEN** exit code is 4, not 1

#### Scenario: Non-space backup abort still exits 10
- **WHEN** a run aborts with `BackupAbortError` from a verification failure and no space
  error occurred
- **THEN** exit code is 10
