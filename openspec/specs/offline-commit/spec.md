# Offline Commit

## Purpose

Offline snapshot commit via qemu-img commit — alternative to virsh blockcommit for merging snapshots when the VM is shut off. Executes the commit → child-pivot → delete sequence per snapshot, scoped to a single disk's backing chain.

## Requirements

### Requirement: QemuImgCommitManager implements ILifecycleManager

The system SHALL provide a `QemuImgCommitManager` class implementing `ILifecycleManager` in `qsnap/modules/lifecycle/qemu_img_commit.py`. It SHALL accept `IShell` as its sole constructor dependency. Its `blockcommit()` method SHALL accept keyword-only required arguments `disk: str` and `base_image: Path`. It SHALL NOT use `qemu-img commit -d` (a no-op on QEMU 11.0.2); instead it SHALL use the explicit three-step sequence: commit → child pivot → delete.

#### Scenario: Constructor accepts IShell

- **WHEN** `QemuImgCommitManager(shell=mock_shell)` is instantiated
- **THEN** `isinstance(manager, ILifecycleManager)` is True

#### Scenario: Successful offline commit without child

- **WHEN** `qemu-img commit -b base.qcow2 snap1.qcow2` returns exit code 0
- **AND** no child overlay references snap1 in the disk's snapshot directory
- **THEN** snap1 is deleted (`rm -f snap1.qcow2`)
- **AND** `CommitResult(success=True, committed_snapshot="snap1.qcow2")` is returned

#### Scenario: Successful offline commit with child pivot

- **WHEN** `qemu-img commit -b base.qcow2 snap1.qcow2` succeeds
- **AND** a child overlay in the disk's snapshot directory has backing-filename pointing to snap1
- **THEN** `qemu-img rebase -u -F qcow2 -b base.qcow2 {child}` is executed
- **AND** only after successful rebase is snap1 deleted
- **AND** `CommitResult(success=True, committed_snapshot="snap1.qcow2")` is returned

#### Scenario: qemu-img commit fails

- **WHEN** `qemu-img commit` returns non-zero exit code
- **THEN** `CommitResult(success=False, error=<stderr>)` is returned
- **AND** the failing file is NOT deleted

#### Scenario: Child pivot fails after successful commit

- **WHEN** `qemu-img commit -b base.qcow2 snap1.qcow2` succeeds
- **BUT** `qemu-img rebase` for the child fails
- **THEN** `CommitResult(success=False, committed_snapshot="snap1.qcow2", error=<rebase error>)` is returned
- **AND** the committed file is NOT deleted (preserved to avoid dangling backing references in the child)

#### Scenario: Commit target is the per-disk base image

- **WHEN** `blockcommit()` is called with `disk="vda"` and `base_image=Path("/data/vm_vda.qcow2")`
- **THEN** `qemu-img commit -b /data/vm_vda.qcow2 <snap>` is used (not a VM-level base)

### Requirement: Factory selection of lifecycle manager

`DefaultFactory.create_lifecycle_manager()` SHALL accept an optional `mode: str = "virsh"` parameter. When `mode == "qemu-img"`, it SHALL return `QemuImgCommitManager`. When `mode == "virsh"` (default), it SHALL return `BlockCommitManager`.

#### Scenario: Default mode returns BlockCommitManager

- **WHEN** `factory.create_lifecycle_manager()` is called without mode
- **THEN** a `BlockCommitManager` instance is returned

#### Scenario: Qemu-img mode returns QemuImgCommitManager

- **WHEN** `factory.create_lifecycle_manager(mode="qemu-img")` is called
- **THEN** a `QemuImgCommitManager` instance is returned
