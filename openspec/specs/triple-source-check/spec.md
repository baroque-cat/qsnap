# Spec: triple-source-check

## Purpose

Triple-source verification for `qsnap check` that cross-references qsnap state JSON, disk qcow2 files per disk, and libvirt domain XML to detect inconsistencies (phantom entries, orphans, stale XML, broken chains) without modifying anything — allowing the operator to review and then run `reconcile` to fix.

## Requirements

### Requirement: Triple-source snapshot verification per disk

`Core.check()` SHALL perform triple-source verification for snapshots by cross-referencing three sources of truth: (1) qsnap state JSON (`{vm_name}.json`), (2) disk qcow2 files — backing chains scanned per disk via `scan_backing_chain()` in `qsnap/utils/verification.py`, (3) libvirt domain XML (`virsh dumpxml`). The verification SHALL use the following matrix:

| state_has | disk_has | xml_has | Classification |
|---|---|---|---|
| yes | yes | yes | OK (consistent) |
| yes | no | no | Phantom in state |
| yes | no | yes | Stale domain XML |
| no | yes | yes | Orphan (state incomplete) |
| no | yes | no | Orphan (untracked) |
| no | no | no | OK (legitimately deleted) |

The check SHALL:
1. Read snapshots from `IStateManager.get_snapshots(vm_name)` — state source (each `SnapshotInfo` has a `.disk` field)
2. Iterate all configured disks, detect each disk's active layer via `_detect_active_layer_path(vm, disk.target)`, then run `scan_backing_chain()` on each active layer — disk source (per-disk walking of each chain)
3. Run `virsh dumpxml --domain <vm>` and parse `<source file="...">` from all `<disk>` and `<backingStore>` elements — XML source
4. Run `virsh domblklist --domain <vm>` and verify active layers match newest snapshots **per disk**: group state snapshots by `SnapshotInfo.disk`, select the newest snapshot (max timestamp) independently within each disk group, and compare each domblklist disk's source path ONLY against the newest snapshot of the same disk target. A domblklist disk that has no snapshots in state SHALL be skipped (nothing to compare). The verification SHALL NOT select a single newest snapshot across all disks and compare every domblklist disk against it. Mismatch reports SHALL name the disk target.
5. Cross-reference all three sources using the matrix above

The check SHALL NOT modify state, disk, or XML. It SHALL report all inconsistencies in `CheckResult`.

#### Scenario: All three sources consistent

- **WHEN** state has 3 snapshots across disks, disk chains match, domain XML references all paths
- **AND** domblklist active layers match the newest snapshots per disk
- **THEN** `CheckResult(status="ok", broken_snapshots=[])` is returned

#### Scenario: Phantom snapshot — state has, disk and XML do not

- **WHEN** state has snap2 but the file does not exist on disk
- **AND** domain XML does not reference snap2 (legitimately deleted via blockcommit)
- **THEN** `CheckResult(status="ok")` is returned with a WARNING: "phantom entry in state: snap2"
- **AND** the result notes that reconcile can fix this by removing the state entry

#### Scenario: Stale domain XML — state and disk agree, XML references missing file

- **WHEN** state has snap2 and snap3 (snap1 deleted via blockcommit)
- **AND** disk chains have snap2 and snap3 (snap1 deleted)
- **AND** domain XML `<backingStore>` still references snap1 (stale after offline commit)
- **THEN** `CheckResult(status="broken", broken_snapshots=["snap1"])` is returned
- **AND** the result notes: "stale domain XML — run reconcile to fix"

#### Scenario: Orphan file — disk has, state does not, XML references

- **WHEN** state has snap1 and snap3
- **AND** disk chain has snap1, snap2, and snap3
- **AND** domain XML references snap2 in `<backingStore>`
- **THEN** `CheckResult(status="ok")` is returned with a WARNING: "orphan file snap2 exists on disk and in XML but not in state"

#### Scenario: Legitimate deletion — all three sources agree file is gone

- **WHEN** state does not have snap1 (removed via blockcommit)
- **AND** disk chain does not have snap1 (deleted)
- **AND** domain XML does not reference snap1 (updated by libvirt or `_refresh_domain_backing_store`)
- **THEN** `CheckResult(status="ok")` is returned — no alarm

#### Scenario: Broken backing chain — file missing from middle

- **WHEN** state has snap1, snap2, snap3
- **AND** disk chain has snap1 and snap3 (snap2 deleted externally)
- **AND** `scan_backing_chain()` reports broken_files
- **THEN** `CheckResult(status="broken", broken_snapshots=["snap2"])` is returned
- **AND** a CRITICAL log is emitted: "chain broken at snap2 — blockcommit impossible, restore from backup"

#### Scenario: Active layer mismatch

- **WHEN** state's newest snapshot for a disk is snap3
- **AND** `virsh domblklist` shows source = snap2 (not snap3) for that disk
- **THEN** `CheckResult(status="broken")` is returned with issue="domblklist active layer ≠ newest snapshot in state" naming the disk target

#### Scenario: Multi-disk VM — each disk compared against its own newest snapshot

- **WHEN** a VM has disks `vda` and `vdb`; state's newest `vda` snapshot is `snapA` (newer timestamp) and newest `vdb` snapshot is `snapB` (older timestamp)
- **AND** `virsh domblklist` shows `vda` source = `snapA` and `vdb` source = `snapB`
- **THEN** `CheckResult(status="ok")` is returned — `vdb` is NOT flagged even though `snapB` is older than `snapA`

#### Scenario: Disk without snapshots is skipped

- **WHEN** `virsh domblklist` lists a disk target that has no snapshots recorded in state
- **THEN** the active-layer comparison for that disk is skipped
- **AND** no mismatch is reported for it

### Requirement: Triple-source target verification

`Core.check()` SHALL perform triple-source verification for backup targets by cross-referencing: (1) qsnap state JSON (`_full_backups.json`, `_dependencies.json`), (2) disk qcow2 files in `target.path`, (3) libvirt checkpoints (`virsh checkpoint-list`).

The check SHALL:
1. Read FULLs from `IStateManager.get_full_backups(target_path)` and incrementals from `IStateManager.get_incremental_dependencies(target_path, full_name)`
2. List backup files on disk via `provider.list(target)`
3. Run `scan_backing_chain()` on the last incremental per chain (single call traverses to FULL)
4. Run `virsh checkpoint-list --name --domain <vm>` and filter by `qsnap-{target_hash}-` prefix
5. Verify: each FULL in state exists on disk, each incremental in state exists on disk, each file on disk is tracked in state, each chain is traversable, exactly one checkpoint per target

#### Scenario: All targets consistent

- **WHEN** state has 1 FULL and 2 incrementals
- **AND** disk has FULL + inc1 + inc2
- **AND** `scan_backing_chain()` on inc2 traverses to FULL
- **AND** `virsh checkpoint-list` shows 1 checkpoint with matching target_hash
- **THEN** `CheckResult(status="ok")` is returned

#### Scenario: Phantom FULL — state has, disk does not

- **WHEN** state has FULL1 but the file does not exist on disk
- **THEN** `CheckResult(status="broken")` is returned with phantom FULL detected

#### Scenario: Broken backup chain — incremental's backing file missing

- **WHEN** state has FULL + inc1 + inc2
- **AND** disk has FULL + inc2 (inc1 deleted)
- **AND** `scan_backing_chain()` on inc2 reports broken_files (backing file inc1 missing)
- **THEN** `CheckResult(status="broken")` is returned with CRITICAL: "backup chain broken at inc1"

#### Scenario: Orphan checkpoint — target_hash does not match

- **WHEN** `virsh checkpoint-list` shows `qsnap-deadbeef-...` but the configured target has hash `a1b2c3d4`
- **THEN** `CheckResult` reports orphan checkpoint detected

#### Scenario: Missing checkpoint — no baseline for next incremental

- **WHEN** state has FULL + inc1 (should have a checkpoint)
- **AND** `virsh checkpoint-list` returns no `qsnap-` checkpoints for this VM
- **THEN** `CheckResult` reports WARNING: "no checkpoint for target, next incremental impossible"

### Requirement: Check is read-only

`Core.check()` SHALL NOT modify state JSON files, disk files, or domain XML. The only side effect SHALL be writing the `_last_deep_check.json` timestamp file when `--deep` mode is used. All inconsistencies SHALL be reported in `CheckResult` for the operator to review and act upon via `reconcile`.

#### Scenario: Check does not modify state

- **WHEN** `Core.check()` detects a phantom snapshot
- **THEN** the state JSON file is NOT modified
- **AND** the phantom is reported in `CheckResult`

#### Scenario: Check does not delete files

- **WHEN** `Core.check()` detects an orphan file
- **THEN** the file is NOT deleted
- **AND** the orphan is reported in `CheckResult`

### Requirement: Shallow check uses scan_backing_chain

The shallow check (default, no `--deep` flag) SHALL delegate chain integrity verification to `scan_backing_chain()` in `qsnap/utils/verification.py`. This function parses the JSON output of `qemu-img info --backing-chain --output=json` and verifies: (a) every file in the chain exists, (b) every file has format `"qcow2"`, (c) `backing-filename` references are consistent, (d) no cycles. The shallow check SHALL NOT rely solely on the command's exit code.

#### Scenario: Shallow check detects inconsistent backing-filename

- **WHEN** `scan_backing_chain()` runs and the JSON output shows a `backing-filename` that does not match the next file in the chain
- **THEN** the inconsistency is reported in `ChainScanResult.broken_files`
- **AND** `CheckResult` reports the issue
