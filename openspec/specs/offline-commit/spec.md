# Offline Commit

## Purpose

Offline snapshot commit via qemu-img commit — alternative to virsh blockcommit for merging snapshots when libvirt is unavailable or blocked.

## Requirements

### Requirement: QemuImgCommitManager implements ILifecycleManager

The system SHALL provide a `QemuImgCommitManager` class implementing `ILifecycleManager` in `qsnap/modules/lifecycle/qemu_img_commit.py`. It SHALL accept `IShell` as its sole constructor dependency. It SHALL use `qemu-img commit -b <base> -d <top>` instead of `virsh blockcommit`.

#### Scenario: Constructor accepts IShell

- **WHEN** `QemuImgCommitManager(shell=mock_shell)` is instantiated
- **THEN** `isinstance(manager, ILifecycleManager)` is True

#### Scenario: Successful qemu-img commit

- **WHEN** `qemu-img commit -b base.qcow2 -d snap1.qcow2` returns exit code 0
- **THEN** `CommitResult(success=True, committed_snapshot="snap1.qcow2")` is returned
- **THEN** the snapshot data is merged into the base image

#### Scenario: qemu-img commit fails

- **WHEN** `qemu-img commit` returns non-zero exit code
- **THEN** `CommitResult(success=False, error=<stderr>)` is returned

### Requirement: Factory selection of lifecycle manager

`DefaultFactory.create_lifecycle_manager()` SHALL accept an optional `mode: str = "virsh"` parameter. When `mode == "qemu-img"`, it SHALL return `QemuImgCommitManager`. When `mode == "virsh"` (default), it SHALL return `BlockCommitManager`.

#### Scenario: Default mode returns BlockCommitManager

- **WHEN** `factory.create_lifecycle_manager()` is called without mode
- **THEN** a `BlockCommitManager` instance is returned

#### Scenario: Qemu-img mode returns QemuImgCommitManager

- **WHEN** `factory.create_lifecycle_manager(mode="qemu-img")` is called
- **THEN** a `QemuImgCommitManager` instance is returned
