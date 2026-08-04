# Tree Listing

## Purpose

Tree-format snapshot listing — displays qcow2 backing chains as an indented hierarchy grouped by disk. Each disk's chain is rooted at its base image with snapshots indented beneath, showing parent-child relationships.

## Requirements

### Requirement: Tree-format snapshot listing grouped by disk

The `list snapshots` subcommand SHALL accept a `--tree` flag. When present, the CLI SHALL display the backing chain as an indented tree grouped by disk target. Each disk's chain SHALL be rooted at its base image, with snapshots indented beneath in order. This reflects the multi-disk model where each disk has its own independent snapshot chain.

#### Scenario: Tree output for a single disk with 3-level chain
- **WHEN** `qsnap list snapshots --tree` is executed on a VM with one disk `vda` and base ← snap1 ← snap2
- **THEN** output shows:
  ```
  === vm1 ===
  [vda] disk.qcow2
    snap1.qcow2
      snap2.qcow2
  ```

#### Scenario: Tree output for multiple disks
- **WHEN** `qsnap list snapshots --tree` is executed on a VM with disks `vda` (2 snapshots) and `vdb` (1 snapshot)
- **THEN** output shows:
  ```
  === vm1 ===
  [vda] vda.qcow2
    vda_snap1.qcow2
      vda_snap2.qcow2
  [vdb] vdb.qcow2
    vdb_snap1.qcow2
  ```

#### Scenario: Flat output without --tree
- **WHEN** `qsnap list snapshots` is executed without `--tree`
- **THEN** output is the standard flat table format with columns including `DISK`

### Requirement: Global --long / -L flag

The CLI SHALL accept a global `--long` / `-L` flag as a shortcut for `--format long`. All subcommands that support `--format` SHALL accept `-L`.

#### Scenario: -L with list command
- **WHEN** `qsnap -L list snapshots` is executed
- **THEN** output is in long format (all available columns displayed)
