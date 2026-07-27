## MODIFIED Requirements

### Requirement: Dirty-block copy loop replaces qemu-img convert for incrementals

`BitmapBackupProvider.transfer_missing()` SHALL transfer incremental data via an in-process copy loop using `INbdClient` instead of `qemu-img convert`. The loop SHALL: (1) resolve the previous backup at the target by walking backwards through the backup list (sorted ascending by timestamp) and selecting the newest backup with an intact backing chain, verifying it still exists immediately before use, (2) create `<name>.qcow2.tmp` via `qemu-img create -f qcow2 -b <previous> -F qcow2` through `IShell`, (3) serve the `.tmp` file through a forked `qemu-nbd` with `--pid-file` and a process-unique write socket, (4) connect the source client to the libvirt socket requesting `base:allocation` and `qemu:dirty-bitmap:backup-<disk>` meta-contexts, (5) query block status, unify extents, and intersect with allocation, (6) `pread` each remaining dirty extent from the source and `pwrite` it to the destination at the same offset, (7) disconnect both clients, terminate `qemu-nbd` via its pidfile, and (8) atomically `mv <name>.qcow2.tmp <name>.qcow2`. The provider SHALL receive the `INbdClient` as a constructor dependency (third parameter after `shell` and `state`); `DefaultFactory` SHALL construct the production `LibnbdClient`. If the previous backup disappears between listing and creation, the transfer SHALL fail with a retryable-class error.

The backwards walk in step (1) SHALL validate backing-chain integrity of each candidate via `qemu-img info --backing-chain --output=json` (which traverses the entire chain and fails if any file is missing). FULL backups (standalone files with no backing) SHALL always be considered valid and skip the chain validation. If no valid non-FULL backup is found, the walk SHALL fall back to the most recent FULL. If no valid backup of any kind is found, the transfer SHALL fail with an error message directing the user to run `qsnap check --deep` and `qsnap reconcile`.

#### Scenario: Incremental copies only dirty blocks
- **WHEN** a prior checkpoint exists and the guest wrote 100 MiB since then
- **THEN** the copy loop reads approximately 100 MiB from the source NBD export (not the full virtual disk size)
- **AND** the resulting qcow2 chains to the previous backup

#### Scenario: First incremental chains to the FULL
- **WHEN** no previous incremental exists at the target
- **THEN** the `.tmp` file is created with `qemu-img create -b <FULL> -F qcow2`
- **AND** the final file's `backing-filename` names the FULL backup

#### Scenario: Previous backup vanished — retryable failure
- **WHEN** the previous backup file is deleted between listing and `qemu-img create`
- **THEN** the transfer returns `BackupResult(success=False, ...)` with an error class Core treats as retryable
- **AND** the standard failure path runs (partial cleanup, successor checkpoint deleted best-effort)

#### Scenario: Broken-chain newest backup skipped — walk to valid previous
- **WHEN** the newest backup at the target has a broken backing chain (its backing file was deleted)
- **THEN** the backwards walk skips the broken-chain file
- **AND** selects the next-newest backup with an intact backing chain as `previous`
- **AND** a WARNING is logged for each skipped broken-chain file

#### Scenario: All non-FULL backups broken — fall back to FULL
- **WHEN** all non-FULL backups at the target have broken backing chains
- **AND** a FULL backup exists
- **THEN** the walk selects the FULL as `previous`
- **AND** the delta is created with `qemu-img create -b <FULL> -F qcow2`

#### Scenario: No valid backup found — error with guidance
- **WHEN** no backup at the target has an intact backing chain (all incrementals broken AND no FULL exists)
- **THEN** the transfer returns `BackupResult(success=False, ...)` with an error message directing the user to run `qsnap check --deep` and `qsnap reconcile`

## ADDED Requirements

### Requirement: Backing-chain validation method for backup files

`BitmapBackupProvider` SHALL provide a `_validate_backing_chain(path: Path) -> bool` method that checks whether a backup file has an intact backing chain. The method SHALL run `qemu-img info --force-share --backing-chain --output=json <path>` via `IShell.run()` and return `True` if the command succeeds (exit code 0) and `False` otherwise. Standalone files (FULLs with no backing file) SHALL be considered valid (the command succeeds on standalone files). The method SHALL NOT raise exceptions — all failures return `False`.

#### Scenario: Valid backing chain returns True
- **WHEN** `_validate_backing_chain(path)` is called on a file whose entire backing chain is intact
- **THEN** the method returns `True`

#### Scenario: Broken backing chain returns False
- **WHEN** `_validate_backing_chain(path)` is called on a file whose backing file has been deleted
- **THEN** the method returns `False`

#### Scenario: Standalone FULL returns True
- **WHEN** `_validate_backing_chain(path)` is called on a FULL backup (no backing file)
- **THEN** the method returns `True`
