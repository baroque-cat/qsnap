# Restore Points Listing

## Purpose

Provides `qsnap list restore-points <vm>` — a read-only enumeration of the real restore points (backup chain termini, each with parsed freeze timestamp and disk) available on every configured target, grouped per target and per disk and sorted by timestamp. Restore points reflect physical file coverage only; snapshot timestamps never appear as restore points.

## Requirements

### Requirement: Restore points listing command

The system SHALL provide `qsnap list restore-points <vm>` that enumerates the real restore points available on every target configured for the VM. A restore point is the freeze timestamp of a backup chain terminus: each FULL and each delta file present on a target, with its parsed timestamp and disk. Output SHALL group entries by target, then by disk, sorted by timestamp, and SHALL mark FULL anchors. The command SHALL be read-only (no state writes, no libvirt mutations).

#### Scenario: Listing shows freeze points per target

- **WHEN** `qsnap list restore-points myvm` is executed and target T holds a FULL (2026-08-07T03:01:34, vda) and a delta (2026-08-08T03:15:42, vda)
- **THEN** the output lists both points under target T with disk `vda`, timestamps, and a FULL marker on the anchor
- **AND** entries are sorted by timestamp

#### Scenario: Empty target reports no points

- **WHEN** the VM has a configured target with no backup files
- **THEN** the command reports that the target has no restore points
- **AND** exits successfully

#### Scenario: Multiple disks distinguished

- **WHEN** the target holds backups for disks `vda` and `vdb`
- **THEN** restore points are listed per disk and never merged across disks

### Requirement: Restore points reflect physical coverage

The listing SHALL present restore points exactly as the backup files exist on the target (parsed from file names and backing chains). It SHALL NOT present snapshot timestamps as restore points and SHALL NOT consult snapshot state. Operators SHALL be able to read the true RPO (spacing between points) from the output.

#### Scenario: Snapshot timestamps never appear as restore points

- **WHEN** snapshots exist in state but no backup files exist on a target
- **THEN** the target's restore point list is empty
- **AND** no snapshot name or timestamp is displayed as a restore point
