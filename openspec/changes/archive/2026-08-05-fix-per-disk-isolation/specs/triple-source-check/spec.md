## MODIFIED Requirements

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
