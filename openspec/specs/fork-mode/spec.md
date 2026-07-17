# Fork Mode

## Purpose

One-command creation of a fully independent, self-sufficient VM from any qsnap-managed snapshot or backup. Uses `qemu-img convert` to flatten the backing chain into a single standalone qcow2 file, then defines a new libvirt VM. The resulting VM has no backing dependencies on the source VM — it is immune to source snapshot deletion.

## Requirements

### Requirement: qsnap fork command creates independent VM from snapshot
`qsnap fork <snapshot-name> --as-vm <new-vm-name>` SHALL locate the named snapshot (via `IStateManager` and backup providers, reusing `Core.restore()` resolution logic), create a standalone qcow2, and define a new libvirt VM using `virsh define` with a modified copy of the source VM's XML.

The standalone qcow2 creation SHALL detect VM running state via `virsh dominfo`. When the VM is running, the method SHALL use the NBD pull-model (`virsh backup-begin` without `--incremental` + `qemu-img convert -n nbd:unix:<socket>`) to avoid lock conflicts on the active layer. When the VM is stopped, the method SHALL use direct `qemu-img convert -O qcow2 <snapshot-path> <target-path>`.

The chain-size estimation step (`qemu-img info --backing-chain`) SHALL use `--force-share` because the source snapshot may be the active layer of a running VM.

#### Scenario: Fork creates standalone writable qcow2 (stopped VM)
- **WHEN** `qsnap fork myvm.20260701T1200 --as-vm myvm-clone --storage /var/lib/libvirt/images/` is executed
- **AND** `virsh dominfo` returns `State: shut off`
- **THEN** `qemu-img convert -O qcow2 /source/snapshots/myvm.20260701T1200.qcow2 /var/lib/libvirt/images/myvm-clone/myvm-clone.qcow2` is executed
- **THEN** the resulting file has NO backing file (`qemu-img info` shows `backing file: <none>`)
- **THEN** the file is writable

#### Scenario: Fork creates standalone writable qcow2 (running VM)
- **WHEN** `qsnap fork myvm.20260701T1200 --as-vm myvm-clone --storage /var/lib/libvirt/images/` is executed
- **AND** `virsh dominfo` returns `State: running`
- **THEN** `virsh backup-begin` is called without `--incremental` to start NBD export
- **THEN** `qemu-img convert -n nbd:unix:<socket> /var/lib/libvirt/images/myvm-clone/myvm-clone.qcow2` is executed
- **THEN** the resulting file has NO backing file
- **AND** no lock conflict occurs

#### Scenario: Fork defines new libvirt VM
- **WHEN** `qsnap fork ... --as-vm myvm-clone` completes convert successfully
- **THEN** `virsh dumpxml` is called on the source VM to obtain XML
- **THEN** the XML is modified: `<name>` changed to "myvm-clone", new `<uuid>` generated, `<source file="...">` updated to the new disk path, `<mac address="...">` removed
- **THEN** `virsh define <xml-path>` is called and succeeds

#### Scenario: Fork from backup
- **WHEN** `qsnap fork backup.20260701T1200 --as-vm recovered-vm` is executed and the snapshot exists on a backup target
- **THEN** the backup file is resolved via backup provider listing (same as `qsnap restore`)
- **THEN** `qemu-img convert` is run on the backup file (which reads its chain from the backup target)
- **THEN** the resulting VM is defined

#### Scenario: Fork with --add-to-config
- **WHEN** `qsnap fork ... --add-to-config` is specified
- **THEN** a new `[[vm]]` block is appended to the qsnap config file:
  ```
  [[vm]]
  name = "myvm-clone"
  base_image = "/var/lib/libvirt/images/myvm-clone/myvm-clone.qcow2"
  snapshot_dir = "/var/lib/libvirt/images/myvm-clone/snapshots"
  snapshot_create = "always"
  ```
- **THEN** `snapshot_dir` is created if it does not exist

### Requirement: Core.fork method
`Core` SHALL provide a `fork(snapshot_name: str, new_vm_name: str, storage_dir: Path, add_to_config: bool = False, vm_filter: str | None = None) -> RestoreResult` method. It SHALL reuse `Core.restore()` for snapshot resolution, then create the standalone qcow2 via `IShell`, then create the VM via `virsh dumpxml` + XML modification + `virsh define`.

#### Scenario: fork returns RestoreResult on success
- **WHEN** `core.fork("myvm.20260701T1200", "myvm-clone", Path("/var/lib/libvirt/images"), add_to_config=False)` completes
- **THEN** returns `RestoreResult(success=True, snapshot_name="myvm.20260701T1200", restored_path=Path("/var/lib/libvirt/images/myvm-clone/myvm-clone.qcow2"), chain_files=[restored_path], error=None)`

#### Scenario: fork fails on nonexistent snapshot
- **WHEN** `core.fork("nonexistent-snap", ...)` is called
- **THEN** returns `RestoreResult(success=False, error="Snapshot not found: nonexistent-snap")`

### Requirement: Fork generates unique VM UUID
The forked VM SHALL receive a newly generated UUID via `uuid.uuid4()`. It SHALL NOT reuse the source VM's UUID.

#### Scenario: Forked VM has different UUID
- **WHEN** fork creates a VM from source VM with UUID "abc123"
- **THEN** the new VM's XML contains a different UUID (not "abc123")

### Requirement: Fork logs estimated size before converting
Before running `qemu-img convert`, fork SHALL estimate and log the expected size of the resulting standalone file (sum of `actual-size` of all files in the snapshot's backing chain). The chain-size estimation step (`qemu-img info --backing-chain`) SHALL use `--force-share` because the source snapshot may be the active layer of a running VM.

#### Scenario: Size estimate logged
- **WHEN** `qsnap fork ...` is executed
- **THEN** an INFO log message shows: "Converting snapshot myvm.20260701T1200 (chain size: ~12.3 GiB) to standalone qcow2..."

#### Scenario: Fork chain-size estimation uses --force-share
- **WHEN** `qsnap fork ...` estimates chain size via `qemu-img info --backing-chain`
- **AND** the source snapshot is the active layer of a running VM
- **THEN** `--force-share` is included in the `qemu-img info` command
- **AND** the command succeeds despite the VM holding a write lock

### Requirement: qsnap deploy command deploys backup as VM
`qsnap deploy <backup-name> --as-vm <new-vm-name> [--storage <dir>]` SHALL be a thin wrapper around fork semantics: locate the backup via restore resolution, convert to standalone qcow2, define VM. If the backup is already a FULL (standalone), `qemu-img convert` SHALL still be called (it is a no-op copy for standalone files, ensuring consistent behavior).

#### Scenario: Deploy FULL backup
- **WHEN** `qsnap deploy vm.FULL.20260701.monthly --as-vm recovered-vm` is executed
- **THEN** the FULL file is copied to `<storage>/recovered-vm/recovered-vm.qcow2`
- **THEN** a new VM is defined

#### Scenario: Deploy incremental backup
- **WHEN** `qsnap deploy vm.20260715T1200 --as-vm recovered-vm` is executed and the backup is an incremental (has backing dependencies on the target)
- **THEN** `qemu-img convert` flattens the chain into a standalone qcow2
- **THEN** a new VM is defined
