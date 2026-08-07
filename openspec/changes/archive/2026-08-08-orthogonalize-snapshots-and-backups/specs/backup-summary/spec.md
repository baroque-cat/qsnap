# Backup Summary — delta

## MODIFIED Requirements

### Requirement: Summary lines carry disk prefix

The summary formatter SHALL render a disk prefix `[<disk>]` immediately after the action
symbol on every line whose `ActionRecord.disk` is not `None` (e.g. `+++ [vda]
testvm.20260701T120000_vda_a1b2c3`). Backup-failure error lines SHALL be disk-scoped:
`ActionRecord(action="error")` produced by a backup failure SHALL carry the failed disk and
the target path, and SHALL be rendered with the disk prefix and a message attributed to the
target and disk (never framed as a snapshot failure). Lines whose `ActionRecord.disk` is
`None` (whole-VM failures such as broken chains or snapshot-creation failures) SHALL be
rendered without a disk prefix. The legend section is unchanged.

#### Scenario: Disk-scoped action line shows disk prefix
- **WHEN** `qsnap run` creates a snapshot on disk `vda` and transfers a backup of disk `vdb`
  for VM "testvm"
- **THEN** the "testvm" block shows `+++ [vda] <snapshot_name>` for the created snapshot
- **AND** the "testvm" block shows `>>> [vdb] <backup_name>` for the transferred backup

#### Scenario: Backup failure error line carries disk and target
- **WHEN** `qsnap run` records an `ActionRecord(action="error", disk="vda")` for a backup
  transfer failure on target `/backup/vm/testvm`
- **THEN** the "testvm" block shows `!!! [vda] backup to target /backup/vm/testvm failed —
  <reason>`
- **AND** the message does not mention snapshots

#### Scenario: VM-level error line has no disk prefix
- **WHEN** `qsnap run` records an `ActionRecord(action="error", disk=None)` for a whole-VM
  failure (e.g., broken backing chain)
- **THEN** the "testvm" block shows `!!! <message>` with no disk prefix

#### Scenario: Multi-disk run distinguishes disks in summary
- **WHEN** `qsnap run` on a two-disk VM creates one snapshot per disk (`vda`, `vdb`) and
  blockcommits one old snapshot of `vda`
- **THEN** the summary shows two `+++` lines with distinct `[vda]` and `[vdb]` prefixes
- **AND** the `---` line carries the `[vda]` prefix
