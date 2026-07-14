# Lifecycle Manager

## Purpose

Backing chain lifecycle management via `virsh blockcommit` — merges old qcow2 snapshots back into the base image to shorten the backing chain and release disk space.

## Requirements

### Requirement: Blockcommit snapshots into base image
The system SHALL merge (blockcommit) specified snapshots into the base image via `virsh blockcommit --delete --verbose --wait`. For each VM disk, the system SHALL determine the disk path via `virsh domblklist`. The base image SHALL be taken from `VMConfig.base_image`. When blockcommit is blocked by AppArmor (stderr contains "Permission denied" or "apparmor") or SELinux (stderr contains "Operation not permitted" or "AVC"), the module SHALL return `CommitResult(success=False, committed_snapshot="", error="blocked by apparmor|selinux")`.

#### Scenario: Successful blockcommit of a single snapshot
- **WHEN** `virsh blockcommit --domain <vm> --path <disk> --base <base> --top <snap> --delete --verbose --wait` returns exit code 0
- **THEN** the module returns `CommitResult(success=True, committed_snapshot=<snapshot.name>)`

#### Scenario: Blockcommit fails — virsh returns error
- **WHEN** `virsh blockcommit` returns a non-zero exit code
- **THEN** the module returns `CommitResult(success=False, error=<stderr from virsh>)`

#### Scenario: Blockcommit blocked by AppArmor
- **WHEN** `virsh blockcommit` stderr contains "Permission denied" and "apparmor"
- **THEN** `CommitResult(success=False, committed_snapshot="", error="blocked by apparmor")` is returned

#### Scenario: Blockcommit blocked by SELinux
- **WHEN** `virsh blockcommit` stderr contains "Operation not permitted" and "AVC"
- **THEN** `CommitResult(success=False, committed_snapshot="", error="blocked by selinux")` is returned

#### Scenario: Empty snapshot list — nothing to merge
- **WHEN** `snapshots_to_merge` is an empty list
- **THEN** the module returns `CommitResult(success=True, committed_snapshot="")`

#### Scenario: Blockcommit times out
- **WHEN** `virsh blockcommit` exceeds the timeout (3600 seconds for large disks)
- **THEN** the module returns `CommitResult(success=False)` with error containing "timed out"

### Requirement: Blockcommit of multiple snapshots

The system SHALL handle multiple snapshots for merging, executing blockcommit for each in order (nearest-to-base first). Each invocation SHALL use `--base <vm_config.base_image>` and `--top <snapshot.path>`.

#### Scenario: Two snapshots merged sequentially

- **WHEN** `snapshots_to_merge` contains [snap1, snap2]
- **THEN** blockcommit is executed for snap1 first (closest to base)
- **AND** then blockcommit for snap2
- **AND** if the first blockcommit fails, the second is NOT executed
- **AND** the result contains information about committed snapshots

### Requirement: QemuImgCommitManager implements ILifecycleManager
The system SHALL provide a `QemuImgCommitManager` class implementing `ILifecycleManager` in `qsnap/modules/lifecycle/qemu_img_commit.py`. It SHALL use `qemu-img commit -b <base> -d <top>` instead of `virsh blockcommit`. It SHALL be selectable via `factory.create_lifecycle_manager(mode="qemu-img")`.

#### Scenario: Successful qemu-img commit
- **WHEN** `qemu-img commit -b base.qcow2 -d snap1.qcow2` returns exit code 0
- **THEN** `CommitResult(success=True, committed_snapshot="snap1.qcow2")` is returned

### Requirement: Factory selectable lifecycle manager
`DefaultFactory.create_lifecycle_manager()` SHALL accept an optional `mode: str = "virsh"` parameter. When `mode == "qemu-img"`, it SHALL return `QemuImgCommitManager`. When `mode == "virsh"` (default), it SHALL return `BlockCommitManager`.

#### Scenario: Default mode returns BlockCommitManager
- **WHEN** `factory.create_lifecycle_manager()` is called without mode
- **THEN** a `BlockCommitManager` instance is returned
