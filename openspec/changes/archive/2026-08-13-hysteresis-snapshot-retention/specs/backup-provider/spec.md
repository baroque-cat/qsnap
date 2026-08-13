# Delta: backup-provider (hysteresis-snapshot-retention)

## ADDED Requirements

### Requirement: Pre-backup block-job probe uses target-name addressing and fails open

Before starting a backup for a disk of a running VM, `BitmapBackupProvider.run_backup` SHALL
probe for an active block job via
`virsh blockjob --domain <vm> --path <disk target device name>` (for example `vda`), using the
shared output classifier (`qsnap.utils.blockjob.classify_blockjob_output`) that is also used
by Core's probes. The provider SHALL NOT pass a backing-file or base-image path as `--path`:
with external snapshot chains the domain XML resolves only the active overlay and target
device names, so a base-image path is guaranteed to raise
`invalid argument: disk '...' not found in domain` from libvirtd. Classification semantics:

- `active` — a block job is reported for the disk: the provider SHALL defer this disk's
  backup with the existing deferred-backup result ("blockjob active"), unchanged.
- `none` — no active block job: the backup proceeds.
- `error` — the probe command failed or its output cannot be classified (including unknown
  disk addressing): the provider SHALL log a WARNING identifying the VM and disk and proceed
  with the backup (fail-open). The probe is a safety gate only; a broken probe MUST NOT
  silently disable backups, and MUST NOT abort the VM.

The probe SHALL run with a bounded shell timeout (30 s). A probe timeout is classified as
`error` and therefore fails open with a WARNING.

#### Scenario: Active block job defers the disk backup

- **WHEN** `run_backup` starts for disk `vda` of a running VM
- **AND** the probe `virsh blockjob --domain vm1 --path vda` reports an active block job
- **THEN** the backup for `vda` is deferred with the deferred-blockjob result
- **AND** no NBD export or checkpoint operation is started for the disk

#### Scenario: Probe addressed by target device name resolves on external chains

- **WHEN** the VM disk is an external snapshot chain whose domain source is the active overlay
- **AND** the probe is issued with `--path vda`
- **THEN** libvirtd resolves the disk and answers with a job state or "No current block job"
- **AND** no `disk '...' not found in domain` error is logged by libvirtd

#### Scenario: Probe failure logs a warning and proceeds

- **WHEN** the probe command exits non-zero, times out, or returns unclassifiable output
- **THEN** a WARNING naming the VM and disk is logged
- **AND** the backup for the disk proceeds as if no block job were active
- **AND** the VM is not marked failed because of the probe alone
