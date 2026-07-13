## Requirements

### Requirement: Lockfile acquisition on startup
The system SHALL acquire a lockfile via `fcntl.flock` (non-blocking) at process startup before any pipeline execution. If the lock cannot be acquired, the process SHALL exit with code 3 and print a message to stderr.

#### Scenario: Successful lock acquisition
- **WHEN** the process starts and no other instance holds the lock
- **THEN** the lock is acquired and pipeline execution proceeds

#### Scenario: Lock already held
- **WHEN** the process starts and another instance already holds the lockfile
- **THEN** the process exits with code 3 and prints "Lockfile is held by another qsnap instance"

### Requirement: Lockfile release on exit
The lock SHALL be automatically released on process exit (normal or abnormal). The lockfile path SHALL be configurable via `GlobalConfig.lockfile` (default `None` — no locking) and overridable via `--lockfile` CLI flag.

#### Scenario: Lock released on normal exit
- **WHEN** the pipeline completes successfully or with errors
- **THEN** the lockfile is released and another process can acquire it

#### Scenario: Lock released on crash
- **WHEN** the process is killed (SIGTERM, SIGKILL)
- **THEN** the kernel releases the `flock` automatically

### Requirement: Lockfile path resolution
The lockfile path SHALL be resolved in order: (1) `--lockfile` CLI flag, (2) `GlobalConfig.lockfile` from the config file, (3) no locking if both are `None`.

#### Scenario: Lockfile from CLI overrides config
- **WHEN** the config has `lockfile = "/var/lock/qsnap.lock"` and `--lockfile /run/qsnap.lock` is passed
- **THEN** the lock is acquired on `/run/qsnap.lock`

#### Scenario: No lockfile means no locking
- **WHEN** both config and CLI have no lockfile specified
- **THEN** no lock is acquired and the pipeline proceeds without locking
