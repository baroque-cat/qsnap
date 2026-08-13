# Blockjob Protocol (DELTA)

## MODIFIED Requirements

### Requirement: Shared block-job probe

Core SHALL provide a single shared helper that probes the block-job state of one disk:
`virsh blockjob --domain <vm> --path <disk>` via `IShell` with a 30-second timeout, returning
one of `"none"`, `"active"`, or `"error"`. The `<disk>` argument SHALL be the libvirt TARGET
device name (e.g. `vda`); probing by base-image path is prohibited for domains with external
snapshots because the domain XML resolves only the active overlay as the disk source and a
base path yields `invalid argument: disk ... not found in domain`. Output containing
"No current block job" (or an empty job report) SHALL classify as `"none"`; any output
describing a job SHALL classify as `"active"`; a failed probe call (non-zero exit, timeout,
parse failure) SHALL classify as `"error"`. The output classification logic SHALL live in ONE
shared pure helper (`classify_blockjob_output`) consumed by both Core's probe and the backup
provider's probe so the two cannot drift. The backup-path blockjob probe SHALL use this
classification with target-name addressing; its observable semantics are: `active` defers the
disk's backup for this run; `error` logs a WARNING naming VM, disk, and error, then proceeds
with the backup (fail-open, documented rationale: a fail-closed probe error would permanently
block backups whenever virsh is flaky, while the dangerous commit-over-job direction is
guarded fail-closed on the commit side).

#### Scenario: No job reported

- **WHEN** `virsh blockjob` outputs "No current block job" for disk `vda`
- **THEN** the probe returns `"none"`

#### Scenario: Active job reported

- **WHEN** `virsh blockjob` outputs a job description (e.g. a blockcommit with progress) for disk `vda`
- **THEN** the probe returns `"active"`

#### Scenario: Probe call fails

- **WHEN** `virsh blockjob` exits non-zero or times out
- **THEN** the probe returns `"error"`

#### Scenario: Probe addresses the disk by target name

- **WHEN** any qsnap component probes the block-job state of a disk
- **THEN** the command is `virsh blockjob --domain <vm> --path <target>` with the target device name
- **AND** no probe passes a base image file path as `--path`

#### Scenario: External-snapshot domain resolves the target probe

- **WHEN** the domain has an active external snapshot chain and the probe uses the target name
- **THEN** libvirt resolves the disk and answers with the real job state (no "not found in domain" error)

#### Scenario: Backup path defers on active job

- **WHEN** the backup step probes a disk with an active block job
- **THEN** the disk's backup is deferred exactly as before (INFO log, no baseline update, not a failure)

#### Scenario: Backup path proceeds on probe error with warning

- **WHEN** the backup step's probe call fails (non-zero exit or timeout)
- **THEN** a WARNING naming the VM, disk, and error is logged
- **AND** the backup proceeds for this run
