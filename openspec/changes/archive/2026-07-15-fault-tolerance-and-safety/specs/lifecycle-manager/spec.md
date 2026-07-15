## MODIFIED Requirements

### Requirement: Blockcommit snapshots into base image
The system SHALL merge (blockcommit) specified snapshots into the base image via `virsh blockcommit --delete --verbose --wait`. For each VM disk, the system SHALL determine the disk path via `virsh domblklist`. The base image SHALL be taken from `VMConfig.base_image`. When blockcommit is blocked by AppArmor (stderr contains "Permission denied" or "apparmor") or SELinux (stderr contains "Operation not permitted" or "AVC"), the module SHALL return `CommitResult(success=False, committed_snapshot="", error="blocked by apparmor|selinux")`.

The `blockcommit()` method SHALL accept an optional keyword argument `deep_verify: bool = False`. When `True` and the commit succeeds, the manager SHALL additionally run `qemu-img check --output=json` on the base image. If corruptions are detected, `CommitResult` SHALL be `success=False` with the corruption count.

#### Scenario: Successful blockcommit with deep verify passing
- **WHEN** `blockcommit(deep_verify=True)` succeeds and `qemu-img check` reports 0 corruptions
- **THEN** `CommitResult(success=True)` is returned

#### Scenario: Successful blockcommit but deep verify fails
- **WHEN** `blockcommit(deep_verify=True)` succeeds but `qemu-img check` reports `corruptions: 5`
- **THEN** `CommitResult(success=False, committed_snapshot="", error="deep verify: 5 corruptions in base image")` is returned

#### Scenario: deep_verify=False — no check performed
- **WHEN** `blockcommit(deep_verify=False)` or `deep_verify` is omitted
- **THEN** no `qemu-img check` is executed after commit
