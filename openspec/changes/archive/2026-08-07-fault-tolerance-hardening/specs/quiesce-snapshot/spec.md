# Quiesce Snapshot — Delta

## MODIFIED Requirements

### Requirement: VMConfig snapshot_quiesce field
`VMConfig` SHALL have a `snapshot_quiesce: bool` field with default `False`. When
`True`, Core SHALL create the VM's snapshots through a single batched
`ISnapshotProvider.create_multi()` call with `quiesce=True`, so that ONE guest-agent
freeze/thaw cycle covers ALL disks of the VM. Core SHALL NOT apply quiesce to only the
first disk, and SHALL NOT issue separate per-disk snapshot commands when quiesce is
enabled.

#### Scenario: Quiesce enabled covers all disks in one freeze

- **WHEN** `vm_config.snapshot_quiesce == True` and snapshots are created for disks
  `["vda", "vdb"]`
- **THEN** `ExternalSnapshotProvider.create_multi()` is called once with both specs and
  `quiesce=True`
- **AND** exactly one `virsh snapshot-create-as --quiesce` command is executed covering
  both disks
- **AND** both disks' data is frozen during the same guest-agent freeze window

#### Scenario: Quiesce disabled (default)

- **WHEN** `vm_config.snapshot_quiesce` is unset or `False`
- **THEN** the batched `virsh snapshot-create-as` is called without `--quiesce` for all
  disks

## ADDED Requirements

### Requirement: create_multi accepts quiesce parameter

`ExternalSnapshotProvider.create_multi(vm_config, specs, quiesce)` SHALL accept the
`quiesce: bool` parameter. When `True`, the single batched `virsh snapshot-create-as`
command SHALL include `--quiesce` and SHALL use the extended 180-second timeout
regardless of the number of disks. When `False`, the command SHALL NOT include
`--quiesce` and SHALL use `120 + 30 × (N − 1)` seconds for N disks.

#### Scenario: Batch with quiesce enabled

- **WHEN** `provider.create_multi(vm_config, specs_for_3_disks, quiesce=True)` is called
- **THEN** `virsh snapshot-create-as ... --quiesce` is executed once with three
  `--diskspec` arguments
- **AND** the timeout is 180 seconds

#### Scenario: Batch without quiesce scales timeout with disk count

- **WHEN** `provider.create_multi(vm_config, specs_for_3_disks, quiesce=False)` is called
- **THEN** the command does NOT contain `--quiesce`
- **AND** the timeout is `120 + 30 × 2 = 180` seconds

### Requirement: Quiesce batch failure is all-or-nothing

When a quiesced batch fails (guest agent missing/not responding, timeout, or virsh
error), the provider SHALL return failed results for ALL specs in the batch. It SHALL NOT
silently fall back to a non-quiesced snapshot and SHALL NOT retry without `--quiesce` —
application consistency is a hard requirement.

#### Scenario: Guest agent not installed fails the whole batch

- **WHEN** `create_multi(..., quiesce=True)` is called but the VM has no
  qemu-guest-agent
- **THEN** every `SnapshotResult` in the batch has `success=False` with the virsh error
  about the guest agent
- **AND** no non-quiesced fallback is attempted
