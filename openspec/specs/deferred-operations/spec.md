# Deferred Operations

## Purpose

Deferred blockcommit operations — when AppArmor/SELinux block virsh blockcommit, snapshots are queued in IStateManager and retried on VM shutdown.

## Requirements

### Requirement: Deferred blockcommit queue in IStateManager

`IStateManager` SHALL provide methods for managing a deferred blockcommit queue: `get_deferred_operations(vm_name) -> list[DeferredBlockcommit]`, `add_deferred_blockcommit(vm_name, snapshots, reason)`, `clear_deferred_operations(vm_name)`. `DeferredBlockcommit` SHALL be a frozen dataclass with fields `snapshots: list[str]`, `reason: str` (`"apparmor"` | `"selinux"`), `since: datetime`.

#### Scenario: Add and retrieve deferred blockcommit

- **WHEN** `add_deferred_blockcommit("vm1", ["snap1.qcow2"], "apparmor")` is called
- **THEN** `get_deferred_operations("vm1")` returns a list containing one `DeferredBlockcommit` with snapshots=["snap1.qcow2"], reason="apparmor"

#### Scenario: Clear deferred operations

- **WHEN** `clear_deferred_operations("vm1")` is called after adding two deferred items
- **THEN** `get_deferred_operations("vm1")` returns an empty list

#### Scenario: No deferred operations for VM

- **WHEN** `get_deferred_operations("vm_new")` is called for a VM with no deferred state
- **THEN** the method returns an empty list

### Requirement: AppArmor/SELinux error detection in BlockCommitManager

`BlockCommitManager.blockcommit()` SHALL detect MAC denials from virsh stderr: `"Permission denied"` or `"apparmor"` (AppArmor), `"Operation not permitted"` or `"AVC"` (SELinux). On detection, the method SHALL return `CommitResult(success=False, committed_snapshot="", error="blocked by apparmor|selinux")` without crashing. Core SHALL record the failed snapshots as deferred operations.

#### Scenario: AppArmor blocks blockcommit

- **WHEN** `virsh blockcommit` stderr contains "Permission denied" and "apparmor"
- **THEN** `CommitResult` is returned with error containing "apparmor"
- **THEN** the snapshots are added to the deferred operations queue

#### Scenario: SELinux blocks blockcommit

- **WHEN** `virsh blockcommit` stderr contains "Operation not permitted" and "AVC"
- **THEN** `CommitResult` is returned with error containing "selinux"

#### Scenario: Normal virsh failure (not MAC-related)

- **WHEN** `virsh blockcommit` stderr contains "No such file or directory"
- **THEN** `CommitResult(success=False)` is returned with the original error — no deferral

### Requirement: Core executes deferred operations on VM shutdown

`Core._execute_snapshot_steps()` SHALL check the deferred operations queue BEFORE creating new snapshots. If the VM is `shut off` (`virsh domstate` returns "shut off") AND there are pending blockcommits, Core SHALL execute them via `BlockCommitManager` and clear the queue on success. If the VM is running, deferred operations SHALL be skipped with an INFO log.

#### Scenario: VM shut off — deferred blockcommits executed

- **WHEN** `virsh domstate` returns "shut off" and there are 2 deferred blockcommit snapshots
- **THEN** `BlockCommitManager.blockcommit()` is called with those snapshots
- **THEN** On success, the deferred queue is cleared

#### Scenario: VM running — deferred blockcommits skipped

- **WHEN** `virsh domstate` returns "running" and there are pending deferred blockcommits
- **THEN** an INFO message is logged: "Skipping 2 deferred blockcommits — VM is running"
- **THEN** pipeline continues to change detection and snapshot creation

#### Scenario: Deferred blockcommit still fails on retry

- **WHEN** a deferred blockcommit fails again on a shut-off VM (e.g. disk error)
- **THEN** the error is logged and the deferred entry remains for the next run
- **THEN** pipeline continues (does not abort)
