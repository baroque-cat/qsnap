# Checkpoint Bitmap Health Probe

## Purpose

Read-only health assessment of a libvirt checkpoint's dirty bitmap before the checkpoint is used as a delta baseline. Running VMs are probed via a QMP `query-named-block-nodes` query; stopped VMs via `qemu-img info -U --backing-chain` over the disk's top layer. The probe returns a tri-state verdict (HEALTHY / DEAD / UNKNOWN), never raises, and never blocks a backup by itself — UNKNOWN preserves pre-existing behavior while the reactive backstop heals failures.

## Requirements

### Requirement: Bitmap health probe for running VMs

For a running VM, the backup provider SHALL assess a checkpoint's dirty bitmap via a
read-only QMP query: `virsh qemu-monitor-command --domain <vm> '{"execute":
"query-named-block-nodes"}'`, executed through `IShell` with a bounded timeout. The
checkpoint SHALL be assessed HEALTHY if and only if some block node of the disk's backing
chain advertises a dirty bitmap whose name equals the checkpoint name and whose
`inconsistent` flag is false. If no such bitmap exists on any node of the chain, or the
bitmap is flagged inconsistent, the checkpoint SHALL be assessed DEAD.

#### Scenario: Healthy bitmap reported by QMP

- **WHEN** `query-named-block-nodes` returns a node in the disk chain with a dirty bitmap
  named `qsnap-abc12345-vda-20260808T160755-e1eb7a` and `inconsistent: false`
- **THEN** the probe returns HEALTHY for that checkpoint

#### Scenario: Bitmap missing after unclean shutdown

- **WHEN** the checkpoint `qsnap-abc12345-vda-20260808T160755-e1eb7a` exists in
  `virsh checkpoint-list` but no node of the disk chain advertises a bitmap with that name
- **THEN** the probe returns DEAD

#### Scenario: Bitmap flagged inconsistent

- **WHEN** the node advertises the bitmap but with `inconsistent: true`
- **THEN** the probe returns DEAD

### Requirement: Bitmap health probe for stopped VMs

For a stopped VM (no QMP available), the provider SHALL assess the bitmap via
`qemu-img info -U --backing-chain --output=json` starting from the domain's top-layer file
for the disk, scanning the `dirty-bitmaps` sections of ALL chain nodes. The HEALTHY/DEAD
decision rules SHALL be identical to the running-VM probe. The bitmap is expected in the
layer that was active when the checkpoint was created, which may now be an intermediate
layer.

#### Scenario: Stopped VM healthy bitmap in intermediate layer

- **WHEN** the VM is shut off and `qemu-img info --backing-chain` shows the checkpoint-named
  bitmap in an intermediate layer's `dirty-bitmaps` section
- **THEN** the probe returns HEALTHY

#### Scenario: Stopped VM dead bitmap

- **WHEN** no layer in the chain lists the checkpoint-named bitmap
- **THEN** the probe returns DEAD

### Requirement: Probe result tri-state and failure isolation

The probe SHALL return exactly one of `HEALTHY`, `DEAD`, or `UNKNOWN`. `UNKNOWN` SHALL be
returned when the probe command fails, times out, or yields unparseable output. The probe
SHALL never raise and SHALL never block a backup by itself: an `UNKNOWN` result leaves the
provider on the pre-existing behavior path (attempt the delta; reactive recovery handles a
subsequent `checkpoint inconsistent` failure). All probe commands SHALL be read-only.

#### Scenario: QMP unavailable yields UNKNOWN

- **WHEN** `virsh qemu-monitor-command` exits non-zero or times out
- **THEN** the probe returns UNKNOWN
- **AND** no exception propagates

#### Scenario: Unparseable QMP JSON yields UNKNOWN

- **WHEN** the QMP output cannot be parsed as JSON or lacks the expected structure
- **THEN** the probe returns UNKNOWN

### Requirement: Baseline assessment exposed on IBackupProvider

`IBackupProvider` SHALL expose one read-only assessment method consumable by Core's dry-run
prediction. Given `(vm_config, target, disk)` it SHALL return the baseline status for the
disk (no checkpoint / healthy checkpoint / dead checkpoint / unknown), the newest
checkpoint name when present, the recovery gate outcome when the checkpoint is dead, and a
size estimate for the predicted backup kind. The method SHALL perform no mutations. This is
a BREAKING interface addition: all implementations and mocks SHALL implement it.

#### Scenario: Assessment reports dead checkpoint with gate outcome

- **WHEN** Core's dry-run calls the assessment for a disk whose newest checkpoint has a dead
  bitmap and passing gates
- **THEN** the result reports status dead-checkpoint, the checkpoint name, gates passing,
  and the recovered-delta size estimate
- **AND** no checkpoint, file, or state mutation occurs

#### Scenario: Assessment with no checkpoint

- **WHEN** no qsnap checkpoint exists for the disk
- **THEN** the result reports status no-checkpoint with a FULL size estimate
