# NBD Bitmap Backup — delta

## MODIFIED Requirements

### Requirement: Prior checkpoint discovery is newest-wins per disk

`BitmapBackupProvider` SHALL select the prior checkpoint for an incremental export as the newest `qsnap-{target_hash}-{disk}-*` checkpoint for that specific disk, ordered by the creation timestamp embedded in the checkpoint name. Names whose timestamp cannot be parsed SHALL sort oldest (conservative). Discovery SHALL use `virsh checkpoint-list --name` and SHALL NOT consult `IStateManager` for checkpoint selection. After selection, the provider SHALL assess the selected checkpoint's bitmap health (capability `checkpoint-bitmap-health-probe`). HEALTHY SHALL proceed to the delta export. DEAD SHALL route into bitmap-loss recovery (capability `bitmap-loss-recovery`) instead of attempting the delta. UNKNOWN SHALL attempt the delta as before, relying on the reactive backstop for `checkpoint inconsistent` failures.

`_list_checkpoints_for_target(vm_name, target_hash, disk)` SHALL filter checkpoints by the prefix `qsnap-{target_hash}-{disk}-`.

#### Scenario: Multiple checkpoints — newest selected

- **WHEN** `virsh checkpoint-list --name VM` returns `qsnap-abc-vda-20260720T010000-aaaaaa`, `qsnap-abc-vda-20260721T010000-bbbbbb`, and a foreign checkpoint `manual-one`
- **THEN** the provider selects `qsnap-abc-vda-20260721T010000-bbbbbb` as prior for disk `vda`
- **AND** `manual-one` is ignored (no `qsnap-` prefix match)

#### Scenario: Different disks have separate checkpoint lineages

- **WHEN** checkpoints exist for both `qsnap-abc-vda-*` and `qsnap-abc-vdb-*`
- **THEN** `_list_checkpoints_for_target(vm, "abc", "vda")` returns only the `vda` checkpoints
- **AND** `_list_checkpoints_for_target(vm, "abc", "vdb")` returns only the `vdb` checkpoints

#### Scenario: No checkpoints for a disk — full export

- **WHEN** no `qsnap-{target_hash}-{disk}-*` checkpoint exists for this disk
- **AND** checkpoints for other disks on the same target exist
- **THEN** a full NBD export is performed for this disk with an atomic successor checkpoint
- **AND** `IStateManager` is NOT consulted for this decision

#### Scenario: Healthy checkpoint proceeds to delta

- **WHEN** the newest checkpoint's bitmap probe returns HEALTHY
- **THEN** the delta export proceeds exactly as before

#### Scenario: Dead checkpoint routes to recovery

- **WHEN** the newest checkpoint's bitmap probe returns DEAD
- **THEN** no delta `backup-begin` is attempted against that checkpoint
- **AND** the bitmap-loss recovery path decides between recovered delta and FULL

#### Scenario: Unknown probe result attempts delta

- **WHEN** the bitmap probe returns UNKNOWN
- **THEN** the delta export is attempted as before
- **AND** a subsequent `checkpoint inconsistent` failure is handled by the reactive backstop
