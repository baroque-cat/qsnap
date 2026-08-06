## MODIFIED Requirements

### Requirement: Core._cleanup_failed_checkpoint rollback method
Core SHALL provide a private method `_cleanup_failed_checkpoint(vm_config, target, full_result)` that deletes exactly the single libvirt checkpoint created during a failed FULL attempt, identified by `full_result.checkpoint`. The method SHALL delete that checkpoint via `virsh checkpoint-delete --metadata --domain <vm> <checkpoint>`. When `full_result.checkpoint` is `None` (no checkpoint was created, e.g. a stopped-VM FULL), the method SHALL delete nothing. The method SHALL NOT filter checkpoints by the `qsnap-{target_hash}-*` prefix, SHALL NOT delete checkpoints belonging to other disks, and SHALL NOT delete the previous baseline checkpoint of the same disk. Deletion failure SHALL be non-fatal (WARNING log).

#### Scenario: Checkpoint cleaned up after failed FULL
- **WHEN** FULL verification fails for a running VM and `_cleanup_failed_checkpoint()` is called with `full_result.checkpoint = "qsnap-ab12cd34-vda-20260807T020000-9f8e7d"`
- **THEN** exactly that checkpoint is deleted via `virsh checkpoint-delete --metadata`
- **AND** no orphaned checkpoint from the failed attempt remains for the next `transfer_missing()` call

#### Scenario: Multi-disk rollback leaves other disks untouched
- **WHEN** a FULL for disk `vda` fails verification on a VM whose target also holds baseline checkpoints for disks `vdb` and `vdc`
- **THEN** only the failed attempt's `vda` checkpoint is deleted
- **AND** the `vdb` and `vdc` checkpoints are NOT deleted

#### Scenario: Previous baseline of the failed disk is preserved
- **WHEN** disk `vda` already holds a baseline checkpoint from the last successful transfer and a new FULL attempt for `vda` fails verification
- **THEN** only the successor checkpoint created by the failed attempt is deleted
- **AND** the previous baseline checkpoint remains intact

#### Scenario: Stopped-VM FULL failure deletes nothing
- **WHEN** FULL verification fails after a stopped-VM FULL (which created no checkpoint, `full_result.checkpoint is None`)
- **THEN** `_cleanup_failed_checkpoint()` issues no `virsh checkpoint-delete` call
- **AND** every existing checkpoint remains intact

#### Scenario: Checkpoint deletion failure is non-fatal
- **WHEN** `virsh checkpoint-delete` fails during `_cleanup_failed_checkpoint()`
- **THEN** a WARNING is logged
- **AND** the rollback continues (FULL file removal and state cleanup still complete)
