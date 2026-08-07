# Locking — delta

## MODIFIED Requirements

### Requirement: Lockfile acquisition on startup
The system SHALL acquire a lockfile via `fcntl.flock` (non-blocking) at process startup
before any MUTATING command execution (`run`, `snapshot`, `backup`, `prune`, `restore`,
`reconcile`, `fork`, `deploy`, and any command that writes state or mutates VMs/targets).
READ-ONLY commands (`list`, `stats`, `check`, `schedule`, `estimate`, dry-run invocations of
read-only commands) SHALL NOT acquire the lock — state files are written atomically, so
readers always observe a complete file. If the lock cannot be acquired, the process SHALL
exit with code 3 and print a message to stderr.

#### Scenario: Successful lock acquisition
- **WHEN** a mutating command starts and no other instance holds the lock
- **THEN** the lock is acquired and execution proceeds

#### Scenario: Lock already held
- **WHEN** a mutating command starts and another instance already holds the lockfile
- **THEN** the process exits with code 3 and prints "Lockfile is held by another qsnap
  instance"

#### Scenario: Read-only command runs while lock is held
- **WHEN** `qsnap check` or `qsnap list` runs while a `qsnap run` holds the lock
- **THEN** the read-only command executes normally without waiting for or acquiring the lock

### Requirement: Lockfile release on exit
The lock SHALL be automatically released on process exit (normal or abnormal). The lockfile
path SHALL default to `/var/lib/qsnap/qsnap.lock` when neither config nor CLI specifies one
(the parent directory SHALL be created if missing). Setting `lockfile = "off"` in config (or
`--lockfile off` on the CLI) SHALL explicitly disable locking; any other value is a path.
Locking applies only to mutating commands (see acquisition requirement).

#### Scenario: Lock released on normal exit
- **WHEN** the command completes successfully or with errors
- **THEN** the lockfile is released and another process can acquire it

#### Scenario: Lock released on crash
- **WHEN** the process is killed (SIGTERM, SIGKILL)
- **THEN** the kernel releases the `flock` automatically

#### Scenario: Default lockfile used when unconfigured
- **WHEN** the config has no `lockfile` key and no `--lockfile` flag is given
- **THEN** the lock is acquired on `/var/lib/qsnap/qsnap.lock` for mutating commands

#### Scenario: Explicit off disables locking
- **WHEN** the config sets `lockfile = "off"`
- **THEN** no lock is acquired for any command and a WARNING log notes that locking is
  disabled

### Requirement: Lockfile path resolution
The lockfile path SHALL be resolved in order: (1) `--lockfile` CLI flag, (2)
`GlobalConfig.lockfile` from the config file, (3) the default `/var/lib/qsnap/qsnap.lock`.
The sentinel value `"off"` at any level disables locking. There SHALL be no configuration in
which locking silently disappears by omission — disabling requires the explicit `"off"`.

#### Scenario: Lockfile from CLI overrides config
- **WHEN** the config has `lockfile = "/var/lock/qsnap.lock"` and `--lockfile
  /run/qsnap.lock` is passed
- **THEN** the lock is acquired on `/run/qsnap.lock`

#### Scenario: Off sentinel in config disables locking
- **WHEN** the config has `lockfile = "off"` and no CLI flag is given
- **THEN** no lock is acquired and the pipeline proceeds without locking
