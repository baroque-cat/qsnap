# State Management — delta

## ADDED Requirements

### Requirement: Host boot_id tracking per VM

`IStateManager` SHALL provide `get_boot_id(vm_name) -> str | None` and
`set_boot_id(vm_name, boot_id)` persisting the host boot identifier
(`/proc/sys/kernel/random/boot_id`) in the per-VM state file as an optional field. Core
SHALL record the current boot_id after each fully successful run for the VM. Absence of the
field (pre-feature state files, first run) SHALL be well-defined: readers receive `None`
and consumers SHALL treat it as "unknown", never as an error. The field SHALL be used for
crash-evidence wording only (capability `bitmap-loss-recovery`), never for gating.

#### Scenario: Boot id recorded on successful run

- **WHEN** a run completes successfully for a VM
- **THEN** the current host boot_id is stored in that VM's state

#### Scenario: Boot id change detected across a crash

- **WHEN** state holds boot_id A and the current host boot_id is B ≠ A
- **THEN** consumers can conclude the host restarted since the last successful run

#### Scenario: Missing boot id is unknown, not an error

- **WHEN** `get_boot_id` is called for a VM whose state predates this feature
- **THEN** it returns None and no exception is raised

### Requirement: Per-disk last-commit timestamp tracking

`IStateManager` SHALL provide `get_last_commit_ts(vm_name, disk) -> datetime | None` and
`set_last_commit_ts(vm_name, disk, ts)` persisting, per VM and disk, the timestamp of the
most recent successful blockcommit or `qemu-img commit` affecting that disk's chain. Core
SHALL write the marker immediately after every successful commit. The marker SHALL be
serialized in the per-VM state file as an optional field; absence SHALL mean "unknown" and
consumers (recovery gate G1, capability `bitmap-loss-recovery`) SHALL treat unknown as
"gate failed" (conservative FULL). No migration of existing state files SHALL be required.

#### Scenario: Marker written after successful blockcommit

- **WHEN** a blockcommit for disk `vda` completes successfully
- **THEN** `last_commit_ts[vm][vda]` is set to the commit time

#### Scenario: Marker written after successful offline commit

- **WHEN** a `qemu-img commit` for a stopped VM completes successfully
- **THEN** the same marker is written for that disk

#### Scenario: Absent marker is conservative

- **WHEN** gate G1 reads the marker for a disk with no recorded commit
- **THEN** it receives None and treats the gate as failed
