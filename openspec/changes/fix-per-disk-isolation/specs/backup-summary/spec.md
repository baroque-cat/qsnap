## ADDED Requirements

### Requirement: Summary lines carry disk prefix

The summary formatter SHALL render a disk prefix `[<disk>]` immediately after the action symbol on every line whose `ActionRecord.disk` is not `None` (e.g. `+++ [vda] testvm.20260701T120000_vda_a1b2c3`). Lines whose `ActionRecord.disk` is `None` (VM-level records) SHALL be rendered without a disk prefix, exactly as before. The legend section is unchanged.

#### Scenario: Disk-scoped action line shows disk prefix
- **WHEN** `qsnap run` creates a snapshot on disk `vda` and transfers an incremental of disk `vdb` for VM "testvm"
- **THEN** the "testvm" block shows `+++ [vda] <snapshot_name>` for the created snapshot
- **AND** the "testvm" block shows `>>> [vdb] <backup_name>` for the transferred incremental

#### Scenario: VM-level error line has no disk prefix
- **WHEN** `qsnap run` records an `ActionRecord(action="error", disk=None)` for a whole-VM failure
- **THEN** the "testvm" block shows `!!! <message>` with no disk prefix

#### Scenario: Multi-disk run distinguishes disks in summary
- **WHEN** `qsnap run` on a two-disk VM creates one snapshot per disk (`vda`, `vdb`) and blockcommits one old snapshot of `vda`
- **THEN** the summary shows two `+++` lines with distinct `[vda]` and `[vdb]` prefixes
- **AND** the `---` line carries the `[vda]` prefix
