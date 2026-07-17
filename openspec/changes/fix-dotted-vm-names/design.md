## Context

qsnap's FULL backup creation (`IBackupProvider.create_full_backup()`) fails for VMs with dots in their names. The method extracts the VM name from the snapshot filename using `source_snapshot.name.split(".")[0]`, which truncates `3.Projects_opencode` to `3`. This causes `virsh dominfo --domain 3` to fail, triggering a fallback to direct `qemu-img convert`, which fails with a lock conflict on the running VM's active qcow2 layer.

A secondary bug exists in `parse_timestamp()` (`qsnap/utils/parsing.py`): it tries `%Y%m%dT%H%M%S` — a format that matches none of the three actual timestamp formats (`%Y%m%d`, `%Y%m%dT%H%M`, `%Y%m%dT%H%M%S%z`). It also takes `split(".")[-1]` which includes the `_{disk}` suffix (e.g. `20260717T0431_vda`), making `strptime` fail even if the format were correct. The function always falls back to file mtime for ALL snapshots, not just dotted ones.

The architectural root cause is that `create_full_backup()` violates the project's DI paradigm: unlike `transfer_missing()` which receives `vm_config: VMConfig` as a method parameter, `create_full_backup()` receives only `source_snapshot: SnapshotInfo` and must reverse-engineer the VM name from the snapshot filename.

Snapshot name format: `{vm_config.name}.{timestamp}_{disk}[_N]` where `vm_config.name` may contain dots.

## Goals / Non-Goals

**Goals:**
- Eliminate fragile VM name parsing from `create_full_backup()` by passing `vm_name` as an explicit method parameter
- Fix `parse_timestamp()` to correctly parse all three configured timestamp formats, including the `_{disk}` suffix
- Add test coverage for VM names containing dots (currently zero tests cover this)
- Maintain backward compatibility of state JSON files (no `SnapshotInfo` schema change)

**Non-Goals:**
- Adding `vm_name` field to `SnapshotInfo` dataclass (would require state migration; not needed since `vm_name` flows through method parameters only)
- Refactoring the snapshot name format to use a different separator (would break existing snapshots on disk)
- Fixing FULL backup naming for previously-created truncated files (orphaned `3.FULL.*.qcow2` files will be cleaned by retention)
- Changing `transfer_missing()` or `list()` method signatures (they already receive `vm_config` or don't need the VM name)

## Decisions

### Decision 1: Add `vm_name: str` parameter to `create_full_backup()` — not to `SnapshotInfo`

**Choice**: Add `vm_name: str` as the first positional parameter of `IBackupProvider.create_full_backup()`.

**Rationale**: This follows the established DI pattern — `transfer_missing()` already receives `vm_config: VMConfig` as its first parameter. Both call sites of `create_full_backup()` already have `vm_config.name` available:
- `Core._backup_target()` (line 2363) — receives `vm_config: VMConfig`
- `FileCopyBackupProvider.transfer_missing()` (line 99) — receives `vm_config: VMConfig`

Adding `vm_name` to `SnapshotInfo` was considered but rejected because:
- It would require backward-compatible state JSON deserialization (migration complexity)
- The `list()` methods that create `SnapshotInfo` from file scanning would still need to parse the VM name from the filename — the same fragile parsing we're trying to eliminate
- The VM name is only needed in `create_full_backup()` for `is_vm_running()` and `nbd_full_export()` calls

**Alternatives considered**:
- *Add `vm_config: VMConfig` instead of `vm_name: str`*: Rejected — `create_full_backup()` only uses `vm_config.name`, not any other field. Passing the full dataclass would be over-injection.
- *Fix the split only (`rsplit(".", 2)[0]`)*: Rejected — still fragile, depends on the exact number of dots in the timestamp format, and doesn't address the architectural root cause.
- *Add `vm_name` to `SnapshotInfo`*: Rejected — see rationale above.

### Decision 2: Position `vm_name` as the first parameter (before `source_snapshot`)

**Choice**: `create_full_backup(self, vm_name: str, source_snapshot: SnapshotInfo, target: TargetConfig, ...)`

**Rationale**: Matches the pattern of `transfer_missing(self, vm_config: VMConfig, target: TargetConfig, ...)` where the VM-identifying parameter comes first. This is consistent and predictable.

### Decision 3: Rewrite `parse_timestamp()` with regex-based extraction

**Choice**: Use `re.search()` with three timestamp patterns (long-iso, long, short) in order of specificity, instead of `split(".")[-1]` + `strptime`.

**Rationale**: The snapshot name format `{vm_name}.{timestamp}_{disk}[_N]` is ambiguous when split on `.` because `vm_name` may contain dots. Regex-based extraction finds the timestamp pattern regardless of its position relative to dots:

```
Patterns (tried in order of specificity):
  r"(\d{8}T\d{6}[+-]\d{4})"   → long-iso: 20250713T153123+0200
  r"(\d{8}T\d{4})"             → long: 20250713T1531
  r"(\d{8})"                    → short: 20250713
```

The `_{disk}` suffix (e.g. `_vda`) and collision suffix (e.g. `_1`) are naturally excluded because they don't match the timestamp patterns.

**Alternatives considered**:
- *Fix split to use `rsplit(".", 1)` then strip `_{disk}`*: Rejected — still fragile, requires knowing the exact disk suffix format, and doesn't handle collision suffixes cleanly.
- *Pass timestamp explicitly through `SnapshotInfo`*: The `SnapshotInfo` already has a `timestamp` field, but `list()` methods must populate it from the filename — which is exactly what `parse_timestamp()` does. So fixing `parse_timestamp()` is the root fix.

### Decision 4: Keep `create_full_backup()` as non-abstract method with `NotImplementedError` default

**Choice**: Do not make `create_full_backup()` abstract. Keep the default `NotImplementedError` body.

**Rationale**: This is the existing design — not all backup providers need to support FULL backups. Making it abstract would force all implementations (including future ones) to provide a FULL backup method. The interface test already verifies this behavior.

## Risks / Trade-offs

- **[BREAKING interface change]** → All `create_full_backup()` callers must add `vm_name` as the first positional argument. The type checker (pyright strict) will catch any missed call sites at compile time. The change is mechanical and low-risk.

- **[parse_timestamp behavior change]** → Previously always returned mtime; now returns the actual parsed timestamp. This changes retention bucket alignment for existing backups. This is a bug fix — the previous behavior was incorrect and caused wrong retention decisions. No migration needed; the next pipeline run will use correct timestamps.

- **[Orphaned FULL files]** → FULL backups created with truncated names (e.g. `3.FULL.20260717.qcow2`) will not match the new naming pattern (`3.Projects_opencode.FULL.*.qcow2`) and will be orphaned by retention. Acceptable — they were created by a bug and can be manually deleted.

- **[~40 test call sites to update]** → Mechanical but tedious. Each test call to `create_full_backup()` needs `vm_name` added as the first positional argument. The type checker will verify completeness.
