## ADDED Requirements

### Requirement: TargetConfig incremental_mode field
`TargetConfig` SHALL gain an `incremental_mode: str` field with default value `"file-copy"`. Accepted values SHALL be `"file-copy"` (whole-file copy, current behaviour) and `"bitmap"` (dirty-block extraction via checkpoint). The field SHALL be immutable (`frozen=True`).

#### Scenario: Default incremental_mode is file-copy
- **WHEN** a TargetConfig is created with `path=Path("/mnt/backup/myvm")` and no `incremental_mode`
- **THEN** `target.incremental_mode` is `"file-copy"`

#### Scenario: Explicit bitmap mode
- **WHEN** a TargetConfig is created with `incremental_mode="bitmap"`
- **THEN** `target.incremental_mode` is `"bitmap"`

### Requirement: VMConfig disks field
`VMConfig` SHALL gain an optional `disks: list[str] | None` field (default `None`). When `None`, `Core` SHALL auto-discover all disks via `virsh domblklist`. When a list is provided, only those disks are snapshotted.

#### Scenario: Disks list is None — auto-discovery
- **WHEN** a VMConfig is created without `disks`
- **THEN** `vm_config.disks` is `None`
- **THEN** Core discovers disks dynamically at runtime

#### Scenario: Explicit disk list
- **WHEN** a VMConfig is created with `disks=["vda", "vdb"]`
- **THEN** only `vda` and `vdb` are snapshotted
