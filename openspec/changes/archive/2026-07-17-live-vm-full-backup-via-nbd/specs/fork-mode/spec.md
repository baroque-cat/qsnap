## MODIFIED Requirements

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

#### Scenario: Fork chain-size estimation uses --force-share
- **WHEN** `qsnap fork ...` estimates chain size via `qemu-img info --backing-chain`
- **AND** the source snapshot is the active layer of a running VM
- **THEN** `--force-share` is included in the `qemu-img info` command
- **AND** the command succeeds despite the VM holding a write lock
