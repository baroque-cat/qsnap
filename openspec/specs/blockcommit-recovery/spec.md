# Blockcommit Recovery

## Purpose

Recovers from broken snapshot chains during blockcommit. Instead of skipping the entire blockcommit when a per-disk chain is broken, Core attempts a partial blockcommit of that disk's snapshots before the break point and auto-rebases stuck snapshots after the break point.

## Requirements

### Requirement: Partial blockcommit on broken per-disk chain

When `_verify_backing_chain(vm_config, disk)` returns `ChainVerifyResult(success=False, broken_file=<path>)`, Core SHALL attempt a partial blockcommit for that disk instead of skipping entirely. Core SHALL call `_split_at_break(vm_config, disk, committable, broken_file)` to split the committable snapshots into `before_break` (snapshots before the broken file in the disk's chain) and `stuck` (snapshots at or after the break). Snapshots in `before_break` SHALL be blockcommitted normally via the per-disk lifecycle manager. Snapshots in `stuck` SHALL be auto-rebased via `_auto_rebase_stuck(vm_config, disk, base_image, stuck)` with the disk's own base image.

#### Scenario: Partial blockcommit before break point

- **WHEN** disk `vda`'s chain is `base -> s1 -> s2(broken) -> s3 -> s4 -> active`
- **AND** retention marks s1 and s3 for removal
- **THEN** `_split_at_break()` produces before_break=[s1], stuck=[s3]
- **AND** s1 is blockcommitted (merged into `vda`'s base image)
- **AND** s3 is auto-rebased via `_auto_rebase_stuck(vm_config, "vda", base_image, [s3])`

#### Scenario: No committable snapshots before break

- **WHEN** disk `vda`'s chain is `base -> s1(broken) -> s2 -> s3 -> active`
- **AND** retention marks s1 and s2 for removal
- **THEN** `_split_at_break()` produces before_break=[], stuck=[s1, s2]
- **AND** a CRITICAL log is emitted (nothing can be committed before the break)
- **AND** a `RuntimeError` is raised (broken chain needs operator intervention)

### Requirement: Auto-rebase for stuck snapshots onto the disk's base image

After partial blockcommit, Core SHALL rebase stuck snapshots of the affected disk onto that disk's base image via `_auto_rebase_stuck(vm_config, disk, base_image, stuck)`. For each stuck snapshot (newest first), `qemu-img rebase -u -b {base_image} -F qcow2 {snap.path}` SHALL be executed to skip the broken file and re-chain to the disk's base image. Snapshots whose files no longer exist on disk SHALL be removed from `IStateManager` via `remove_snapshot()`.

#### Scenario: Stuck snapshot rebased to disk's base image

- **WHEN** disk `vda`'s chain is `base -> s1 -> s2(broken) -> s3 -> active`
- **AND** s3's backing-filename points to s2
- **THEN** `qemu-img rebase -u -b <vda_base_image> -F qcow2 <s3_path>` is executed
- **AND** s3's backing-filename now points to vda's base image

#### Scenario: Missing stuck snapshot file cleaned from state

- **WHEN** a stuck snapshot's file no longer exists on disk
- **THEN** `remove_snapshot()` is called for that snapshot
- **AND** no rebase is attempted for the missing file

#### Scenario: Each disk's recovery is independent

- **WHEN** disk `vda`'s chain is broken but disk `vdb`'s chain is intact
- **THEN** partial blockcommit and auto-rebase only apply to `vda`
- **AND** `vdb`'s blockcommit proceeds normally without recovery

### Requirement: Per-disk chain verification reports broken file

`Core._verify_backing_chain(vm_config, disk)` SHALL verify the integrity of one disk's backing chain. `ChainVerifyResult` SHALL include a `broken_file: Path | None` field and a `disk: str` field. When chain verification fails due to a missing file, `broken_file` SHALL be set to the absolute path of the missing file. When verification fails for other reasons (non-qcow2, cycle), `broken_file` SHALL be `None`.

#### Scenario: Broken file reported on missing file

- **WHEN** `qemu-img info --backing-chain` for disk `vda` fails because `s2.qcow2` does not exist
- **THEN** `ChainVerifyResult(success=False, broken_file=Path("/path/to/s2.qcow2"), disk="vda")` is returned

#### Scenario: No broken file on other failures

- **WHEN** chain verification for disk `vda` fails due to a cyclic reference
- **THEN** `ChainVerifyResult(success=False, broken_file=None, disk="vda")` is returned
- **AND** a `RuntimeError` is raised (broken chain needs operator intervention)

### Requirement: Split-at-break walks the disk's backing chain

`Core._split_at_break(vm_config, disk, committable, broken_file)` SHALL walk the backing chain of the specified disk to classify each committable snapshot as either before the break or stuck. When the full `qemu-img info --backing-chain` fails, it SHALL fall back to per-file `qemu-img info` queries to locate the break point. When the broken file cannot be found in the chain, all snapshots SHALL be treated as stuck (conservative).

#### Scenario: Per-file fallback when backing-chain query fails

- **WHEN** `qemu-img info --backing-chain` for disk `vda` fails
- **THEN** each committable snapshot is queried individually via `qemu-img info --output=json`
- **AND** a snapshot whose backing-filename matches the broken file is classified as stuck
- **AND** snapshots before the first stuck snapshot are classified as committable
