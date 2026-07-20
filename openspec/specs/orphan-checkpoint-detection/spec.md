# Orphaned Checkpoint Detection

## Purpose

Detection of libvirt checkpoints that no longer correspond to any configured VM or target. Checkpoints live only in libvirt (not in qsnap state files), so when a VM is removed from config, a target is removed, or a target path changes, checkpoints become permanently orphaned with no automatic cleanup. This spec defines the detection mechanism in `Core.check_state()`.

## Requirements

### Requirement: Orphaned checkpoint detection in check_state

`Core.check_state()` SHALL detect libvirt checkpoints that no longer correspond to any configured VM or target. Checkpoints are named `qsnap-{target_hash}-{snapshot_name}` where `target_hash` is an 8-character MD5 hash of the target path. A checkpoint is orphaned when its `target_hash` does not match `_target_hash(str(target.path))` for any target configured for that VM.

The detection SHALL call `virsh checkpoint-list --name --domain <vm_name>` for each VM in the config, filter by the `qsnap-` prefix, parse the `target_hash` from each checkpoint name, and compare against the set of configured target hashes. Orphaned checkpoints SHALL be reported in the `StateCheckResult.orphan_checkpoints` field as a list of strings.

The detection SHALL be non-fatal — if `virsh checkpoint-list` fails (e.g., VM not defined, libvirt not running), the method SHALL log a WARNING and continue to the next VM. No checkpoints SHALL be deleted automatically — detection is read-only.

#### Scenario: Orphaned checkpoint from removed target
- **WHEN** a VM has a target `/mnt/backup/old` that was removed from config
- **AND** a checkpoint `qsnap-deadbeef-snap1` exists in libvirt (hash `deadbeef` was for `/mnt/backup/old`)
- **AND** `check_state()` is called
- **THEN** `StateCheckResult.orphan_checkpoints` contains `"qsnap-deadbeef-snap1"`
- **AND** a WARNING is logged: "Orphaned checkpoint qsnap-deadbeef-snap1 for VM <vm> — target hash deadbeef matches no configured target"

#### Scenario: Orphaned checkpoint from changed target path
- **WHEN** a VM's target path was changed from `/mnt/backup/old` to `/mnt/backup/new`
- **AND** a checkpoint `qsnap-deadbeef-snap1` exists (hash for old path)
- **AND** the new path produces a different hash
- **AND** `check_state()` is called
- **THEN** `StateCheckResult.orphan_checkpoints` contains `"qsnap-deadbeef-snap1"`

#### Scenario: No orphaned checkpoints when all targets match
- **WHEN** all checkpoints for a VM have `target_hash` values matching configured targets
- **AND** `check_state()` is called
- **THEN** `StateCheckResult.orphan_checkpoints` is an empty list for that VM

#### Scenario: Checkpoint-list command failure is non-fatal
- **WHEN** `virsh checkpoint-list --domain <vm>` fails (e.g., VM not defined in libvirt)
- **THEN** a WARNING is logged: "Failed to list checkpoints for VM <vm>: <error>"
- **AND** `check_state()` continues to the next VM
- **AND** no orphan checkpoints are reported for that VM

#### Scenario: Non-qsnap checkpoints are ignored
- **WHEN** libvirt contains checkpoints `qsnap-deadbeef-snap1` and `manual-checkpoint`
- **AND** `check_state()` is called
- **THEN** only `qsnap-deadbeef-snap1` is evaluated for orphan status
- **AND** `manual-checkpoint` is ignored (does not start with `qsnap-` prefix)

### Requirement: StateCheckResult includes orphan_checkpoints field

`StateCheckResult` SHALL gain an `orphan_checkpoints: list[str]` field (default: empty list). This field SHALL be populated by `Core.check_state()` with the names of all orphaned qsnap-owned checkpoints across all VMs in the config. The field SHALL be included in the CLI output of `qsnap check --state`.

#### Scenario: StateCheckResult with orphaned checkpoints
- **WHEN** `check_state()` detects 2 orphaned checkpoints across 2 VMs
- **THEN** `StateCheckResult.orphan_checkpoints` contains both checkpoint names
- **AND** the CLI output of `qsnap check --state` lists them under an "Orphaned Checkpoints" section

#### Scenario: StateCheckResult with no orphaned checkpoints
- **WHEN** `check_state()` detects no orphaned checkpoints
- **THEN** `StateCheckResult.orphan_checkpoints` is an empty list
- **AND** the CLI output does not show an "Orphaned Checkpoints" section (or shows "None")

### Requirement: Deduplication of FULL backup state entries on load

`JsonStateManager` SHALL deduplicate entries in `_full_backups.json` that have the same `(name, target_path)` tuple. This is a one-time migration to fix the double-recording bug where `BitmapBackupProvider.create_full_backup()` and `Core._backup_target()` both called `record_full_backup()`. The deduplication SHALL occur on load (`_load_full_backups()`) and SHALL preserve the first entry for each `(name, target_path)` tuple. A INFO log SHALL be emitted for each removed duplicate.

#### Scenario: Duplicate FULL entries deduplicated on load
- **WHEN** `_full_backups.json` contains two entries with `name="vm.FULL.20260719"` and `target_path="/mnt/backup"`
- **AND** `JsonStateManager` loads the state
- **THEN** only one entry with that name and target_path remains
- **AND** an INFO log is emitted: "Deduplicated FULL backup entry: vm.FULL.20260719 for target /mnt/backup"

#### Scenario: No duplicates — no deduplication
- **WHEN** `_full_backups.json` contains no duplicate `(name, target_path)` tuples
- **AND** `JsonStateManager` loads the state
- **THEN** no deduplication occurs
- **AND** no INFO log is emitted

#### Scenario: Deduplication is idempotent
- **WHEN** `JsonStateManager` loads state that was already deduplicated
- **THEN** no further deduplication occurs
- **AND** no INFO log is emitted
