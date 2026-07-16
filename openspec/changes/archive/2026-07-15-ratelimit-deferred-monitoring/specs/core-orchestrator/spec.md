## ADDED Requirements

### Requirement: Post-pipeline deferred threshold check

At the end of `Core._run_pipeline()`, the system SHALL call `_check_deferred_thresholds()` which iterates over all VMs, retrieves their deferred operations from `IStateManager`, and compares count and age against `GlobalConfig` thresholds (`deferred_warn_count`, `deferred_crit_count`, `deferred_warn_age`, `deferred_crit_age`). WARNING or CRITICAL log messages SHALL be emitted for threshold violations. The check SHALL NOT affect the pipeline exit code.

#### Scenario: Deferred threshold WARNING logged

- **WHEN** a VM has 5 deferred operations and `deferred_warn_count = "5"`
- **AND** `run()` completes successfully
- **THEN** a WARNING log message is emitted for that VM
- **AND** exit code is 0

#### Scenario: Deferred threshold CRITICAL logged

- **WHEN** a VM has 10 deferred operations and `deferred_crit_count = "10"`
- **AND** `run()` completes successfully
- **THEN** a CRITICAL log message is emitted for that VM

### Requirement: Core.list_deferred() method

Core SHALL expose a `list_deferred(vm_filter=None)` method returning per-VM deferred operation summaries: VM name, snapshot count, reason, and age of the oldest operation. The method SHALL use `IStateManager.get_deferred_operations()` to retrieve data.

#### Scenario: list_deferred returns summaries for all VMs

- **WHEN** `core.list_deferred()` is called with two VMs having deferred operations
- **THEN** the result contains two entries with vm_name, snapshots count, reason, and age

#### Scenario: list_deferred with VM filter

- **WHEN** `core.list_deferred(vm_filter="vm-home")` is called
- **THEN** only the "vm-home" entry is returned

### Requirement: Core.check() includes deferred status with remediation

`Core.check()` SHALL include deferred operation count, age, and reason for each VM. When deferred operations are present, the output SHALL include actionable remediation guidance.

#### Scenario: Check includes deferred status

- **WHEN** `core.check()` is called on a VM with 3 deferred operations (reason: apparmor)
- **THEN** the output includes the deferred count and reason
- **AND** the output includes remediation guidance: "Merge blocked by AppArmor. Consider: aa-disable /etc/apparmor.d/libvirt/libvirt-<uuid>"

## MODIFIED Requirements

### Requirement: Pipeline step order

`Core._execute_pipeline(vm_config)` SHALL execute steps in this order:
1. Pre-flight environment validation
2. Deferred blockcommit check (if VM is shut off)
3. Change detection — if `snapshot_create` mode requires it
4. Snapshot creation — if detector says we should, or if mode is "always"
5. Snapshot retention evaluation — which snapshots to keep/remove
6. Snapshot lifecycle — blockcommit removed snapshots with MAC denial deferral
7. For each target: backup transfer → backup verification → backup retention → cleanup

After all VMs are processed, `_check_deferred_thresholds()` SHALL be called to evaluate accumulated deferred operations against configured warning/critical thresholds.

#### Scenario: Pipeline with always mode

- **WHEN** a VM has `snapshot_create = "always"` and the pipeline runs
- **THEN** validation runs first, then a snapshot is created regardless of change detection result

#### Scenario: Pipeline with onchange mode, no changes

- **WHEN** a VM has `snapshot_create = "onchange"` and the change detector reports `has_changed = False`
- **THEN** no snapshot is created, but retention is still evaluated
