## ADDED Requirements

### Requirement: Triple-source snapshot verification

`Core.check()` SHALL perform triple-source verification for snapshots by cross-referencing three sources of truth: (1) qsnap state JSON (`{vm_name}.json`), (2) disk qcow2 files in `snapshot_dir`, (3) libvirt domain XML (`virsh dumpxml`). The verification SHALL use the following matrix:

| state_has | disk_has | xml_has | Classification |
|---|---|---|---|
| yes | yes | yes | OK (consistent) |
| yes | no | no | Phantom in state |
| yes | no | yes | Stale domain XML |
| no | yes | yes | Orphan (state incomplete) |
| no | yes | no | Orphan (untracked) |
| no | no | no | OK (legitimately deleted) |

The check SHALL:
1. Read snapshots from `IStateManager.get_snapshots(vm_name)` — state source
2. Run `qemu-img info --force-share --backing-chain --output=json` on the active layer — disk source (single call traverses entire chain)
3. Run `virsh dumpxml --domain <vm>` and parse `<disk><source file="...">` and `<backingStore><source file="...">` elements — XML source
4. Run `virsh domblklist --domain <vm>` — verify active layer matches
5. Cross-reference all three sources using the matrix above

The check SHALL NOT modify state, disk, or XML. It SHALL report all inconsistencies in `CheckResult`.

#### Scenario: All three sources consistent

- **WHEN** state has 3 snapshots, disk has 3 files, domain XML references all 3
- **AND** domblklist active layer matches the newest snapshot
- **THEN** `CheckResult(status="ok", broken_snapshots=[])` is returned

#### Scenario: Phantom snapshot — state has, disk and XML do not

- **WHEN** state has snap2 but the file does not exist on disk
- **AND** domain XML does not reference snap2 (legitimately deleted via blockcommit)
- **THEN** `CheckResult(status="ok")` is returned with a WARNING: "phantom entry in state: snap2"
- **AND** the result notes that reconcile can fix this by removing the state entry

#### Scenario: Stale domain XML — state and disk agree, XML references missing file

- **WHEN** state has snap2 and snap3 (snap1 deleted via blockcommit)
- **AND** disk has snap2 and snap3 (snap1 deleted)
- **AND** domain XML `<backingStore>` still references snap1 (stale after offline commit)
- **THEN** `CheckResult(status="broken", broken_snapshots=["snap1"])` is returned
- **AND** the result notes: "stale domain XML — run reconcile to fix"

#### Scenario: Orphan file — disk has, state does not, XML references

- **WHEN** state has snap1 and snap3
- **AND** disk has snap1, snap2, and snap3
- **AND** domain XML references snap2 in `<backingStore>`
- **THEN** `CheckResult(status="ok")` is returned with a WARNING: "orphan file snap2 exists on disk and in XML but not in state"

#### Scenario: Legitimate deletion — all three sources agree file is gone

- **WHEN** state does not have snap1 (removed via blockcommit)
- **AND** disk does not have snap1 (deleted)
- **AND** domain XML does not reference snap1 (updated by libvirt or _refresh_domain_backing_store)
- **THEN** `CheckResult(status="ok")` is returned — no alarm

#### Scenario: Broken backing chain — file missing from middle

- **WHEN** state has snap1, snap2, snap3
- **AND** disk has snap1 and snap3 (snap2 deleted externally)
- **AND** `qemu-img info --backing-chain` fails or shows truncated chain
- **THEN** `CheckResult(status="broken", broken_snapshots=["snap2"])` is returned
- **AND** a CRITICAL log is emitted: "chain broken at snap2 — blockcommit impossible, restore from backup"

#### Scenario: Active layer mismatch

- **WHEN** state's newest snapshot is snap3
- **AND** `virsh domblklist` shows source = snap2 (not snap3)
- **THEN** `CheckResult(status="broken")` is returned with issue="domblklist active layer ≠ newest snapshot in state"

### Requirement: Triple-source target verification

`Core.check()` SHALL perform triple-source verification for backup targets by cross-referencing: (1) qsnap state JSON (`_full_backups.json`, `_dependencies.json`), (2) disk qcow2 files in `target.path`, (3) libvirt checkpoints (`virsh checkpoint-list`).

The check SHALL:
1. Read FULLs from `IStateManager.get_full_backups(target_path)` and incrementals from `IStateManager.get_incremental_dependencies(target_path, full_name)`
2. List backup files on disk via `provider.list(target)`
3. Run `qemu-img info --backing-chain` on the last incremental per chain (single call traverses to FULL)
4. Run `virsh checkpoint-list --name --domain <vm>` and filter by `qsnap-{target_hash}-` prefix
5. Verify: each FULL in state exists on disk, each incremental in state exists on disk, each file on disk is tracked in state, each chain is traversable, exactly one checkpoint per target

#### Scenario: All targets consistent

- **WHEN** state has 1 FULL and 2 incrementals
- **AND** disk has FULL + inc1 + inc2
- **AND** `qemu-img info --backing-chain` on inc2 traverses to FULL
- **AND** `virsh checkpoint-list` shows 1 checkpoint with matching target_hash
- **THEN** `CheckResult(status="ok")` is returned

#### Scenario: Phantom FULL — state has, disk does not

- **WHEN** state has FULL1 but the file does not exist on disk
- **THEN** `CheckResult(status="broken")` is returned with phantom FULL detected

#### Scenario: Broken backup chain — incremental's backing file missing

- **WHEN** state has FULL + inc1 + inc2
- **AND** disk has FULL + inc2 (inc1 deleted)
- **AND** `qemu-img info --backing-chain` on inc2 fails (backing file inc1 missing)
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

### Requirement: Shallow check uses JSON parsing

The shallow check (default, no `--deep` flag) SHALL parse the JSON output of `qemu-img info --backing-chain --output=json` and verify: (a) every file in the chain exists, (b) every file has format `"qcow2"`, (c) `backing-filename` references are consistent, (d) no cycles. The shallow check SHALL NOT rely solely on the command's exit code.

#### Scenario: Shallow check detects inconsistent backing-filename

- **WHEN** `qemu-img info --backing-chain` returns exit code 0
- **AND** the JSON output shows a `backing-filename` that does not match the next file in the chain
- **THEN** `CheckResult(status="broken")` is returned with the inconsistency reported
