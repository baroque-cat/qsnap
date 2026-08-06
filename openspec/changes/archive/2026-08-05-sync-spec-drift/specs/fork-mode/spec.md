## MODIFIED Requirements

### Requirement: Core.fork method
`Core` SHALL provide a `fork(name: str, output_path: Path, vm_filter: str | None = None) -> RestoreResult` method. It SHALL reuse `Core._resolve_snapshot()` for snapshot/backup resolution, estimate chain size, then create the standalone qcow2 via the shared standalone-image-conversion helpers (`convert_with_retry` followed by `verify_standalone_image`), consulting `self._dry_run` after the read-only chain-size estimate. It SHALL NOT perform XML manipulation or VM definition. The returned `RestoreResult` SHALL include `disk` from `snapshot_info.disk`.

The optional `vm_filter` (CLI: `qsnap fork <name> [vm]`) SHALL be passed to `_resolve_snapshot()` to restrict the search to matching VMs: when the filter matches no VM that owns the named snapshot, resolution fails and fork returns `RestoreResult(success=False, error="Snapshot not found: <name>")`. When several VMs own snapshots with identical names, the filter disambiguates which one is used. Resolution failure SHALL return a failed `RestoreResult` even in dry-run mode (the failure is determined before any conversion work).

#### Scenario: fork returns RestoreResult on success
- **WHEN** `core.fork("myvm.20260701T120000_a1b2c3", Path("/var/lib/libvirt/images/clone.qcow2"))` completes
- **THEN** returns `RestoreResult(success=True, snapshot_name="myvm.20260701T120000_a1b2c3", restored_path=Path("/var/lib/libvirt/images/clone.qcow2"), chain_files=[restored_path], error=None, disk="vda")`

#### Scenario: fork fails on nonexistent snapshot
- **WHEN** `core.fork("nonexistent-snap", Path("/tmp/test.qcow2"))` is called
- **THEN** returns `RestoreResult(success=False, error="Snapshot not found: nonexistent-snap")`

#### Scenario: fork does not touch XML or state
- **WHEN** `core.fork(...)` completes successfully
- **THEN** no `virsh dumpxml`, `virsh define`, or `IStateManager` mutation occurs

#### Scenario: fork dry-run logs the plan and creates no file
- **WHEN** `core.fork("myvm.20260701T120000_a1b2c3", Path("/tmp/clone.qcow2"))` is called with `core.dry_run = True`
- **THEN** the read-only chain-size estimate (`qemu-img info --backing-chain --force-share`) still runs
- **AND** an INFO log message states the planned conversion with source, output path, and estimated size
- **AND** no `qemu-img convert` is executed and no output file is created
- **AND** returns `RestoreResult(success=True)`

#### Scenario: fork with non-matching vm filter reports snapshot not found
- **WHEN** `core.fork("myvm.20260701T120000_a1b2c3", Path("/tmp/test.qcow2"), vm_filter="othervm")` is called and only "myvm" owns that snapshot
- **THEN** returns `RestoreResult(success=False, error="Snapshot not found: myvm.20260701T120000_a1b2c3")`

#### Scenario: fork vm filter disambiguates identical snapshot names
- **WHEN** VMs "vm1" and "vm2" both have a snapshot named "shared-name" and `core.fork("shared-name", out, vm_filter="vm2")` is called
- **THEN** "vm2"'s snapshot is resolved and forked

#### Scenario: fork dry-run with unresolvable snapshot still fails
- **WHEN** `core.fork("nonexistent-snap", Path("/tmp/test.qcow2"))` is called with `core.dry_run = True`
- **THEN** returns `RestoreResult(success=False, error="Snapshot not found: nonexistent-snap")`
