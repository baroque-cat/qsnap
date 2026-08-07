## MODIFIED Requirements

### Requirement: VMConfig dataclass
The system SHALL provide an immutable `VMConfig` dataclass representing a single VM's configuration. Required fields SHALL be `name: str` and `disks: list[DiskConfig]` (one or more disks, each carrying its own `base_image`). Optional fields include `snapshot_dir: Path | None = None` (VM-level default snapshot directory), `snapshot_create="always"`, `snapshot_chain_length=None`, `target_chain_length=None`, `target_keep_generations=None`, `snapshot_preserve_min=None`, `snapshot_quiesce=False`, `lifecycle_mode="virsh"`, `change_detection_mode="allocation-map"`, `blockcommit_deep_verify=False`, the backup engine option fields `compress=True`, `compression_type="zstd"`, `convert_parallel=4`, `convert_out_of_order=True`, `backup_stall_timeout="30m"`, `verify="metadata"` (VM-level defaults for inheritance into targets), and `targets=[]`. There SHALL be NO VM-level `base_image` field. `VMConfig` SHALL provide methods `get_disk(target: str) -> DiskConfig | None` and `snapshot_dir_for(disk: DiskConfig) -> Path | None`.

#### Scenario: VMConfig with required fields
- **WHEN** a `VMConfig` is created with `name="myvm"` and `disks=[DiskConfig(target="vda", base_image=Path("..."))]`
- **THEN** the instance has all required fields populated, `snapshot_create` defaults to `"always"`, `blockcommit_deep_verify` defaults to `False`, retention overrides default to `None`, engine options default to `compress=True`, `compression_type="zstd"`, `convert_parallel=4`, `convert_out_of_order=True`, `backup_stall_timeout="30m"`, `verify="metadata"`, and there is no `base_image` field

#### Scenario: VMConfig get_disk finds matching disk
- **WHEN** `vm.get_disk("vda")` is called and a disk with `target="vda"` exists
- **THEN** the matching `DiskConfig` is returned

#### Scenario: VMConfig get_disk returns None for unknown target
- **WHEN** `vm.get_disk("vdz")` is called and no disk with `target="vdz"` exists
- **THEN** `None` is returned

#### Scenario: VMConfig snapshot_dir_for uses per-disk override
- **WHEN** `vm.snapshot_dir_for(disk)` is called and `disk.snapshot_dir` is set to `Path("/overrides/vda")`
- **THEN** `Path("/overrides/vda")` is returned, ignoring the VM-level default

#### Scenario: VMConfig snapshot_dir_for falls back to VM-level
- **WHEN** `vm.snapshot_dir_for(disk)` is called and `disk.snapshot_dir` is `None`, but `vm.snapshot_dir` is `Path("/snapshots")`
- **THEN** `Path("/snapshots")` is returned

#### Scenario: VMConfig snapshot_dir_for returns None when neither is set
- **WHEN** `vm.snapshot_dir_for(disk)` is called and both `disk.snapshot_dir` and `vm.snapshot_dir` are `None`
- **THEN** `None` is returned

#### Scenario: VMConfig with targets
- **WHEN** a `VMConfig` is created with a list of `TargetConfig` objects
- **THEN** `vm.targets` contains those targets in order

#### Scenario: VMConfig disks defensive copy
- **WHEN** a `VMConfig` is created with a list of disks
- **AND** the original list is mutated externally after construction
- **THEN** `vm.disks` is unaffected (defensive copy)

### Requirement: compression_type field in TargetConfig
`TargetConfig` SHALL include a `compression_type: str = "zstd"` field. Valid values are `"zstd"` and `"zlib"`. Resolution follows global → VM → target inheritance: a target without an explicit value inherits the VM-level resolved value, which itself inherits the global value.

#### Scenario: TargetConfig compression_type inherits from global
- **WHEN** a `TargetConfig` is created without explicit `compression_type`
- **AND** the `GlobalConfig` has `compression_type="zstd"` and the VM sets no override
- **THEN** `target.compression_type == "zstd"`

#### Scenario: TargetConfig compression_type inherits from VM
- **WHEN** a `TargetConfig` is created without explicit `compression_type`
- **AND** the VM-level resolved value is `compression_type="zlib"`
- **THEN** `target.compression_type == "zlib"`

#### Scenario: TargetConfig compression_type overrides global
- **WHEN** a `TargetConfig` is created with `compression_type="zlib"`
- **AND** the `GlobalConfig` has `compression_type="zstd"`
- **THEN** `target.compression_type == "zlib"`

### Requirement: backup_stall_timeout field in TargetConfig
`TargetConfig` SHALL include a `backup_stall_timeout: str = "30m"` field. The value is a duration string parsed to seconds via `parse_stall_timeout()`. Resolution follows global → VM → target inheritance: a target without an explicit value inherits the VM-level resolved value, which itself inherits the global value.

#### Scenario: TargetConfig stall timeout inherits from global
- **WHEN** a `TargetConfig` is created without explicit `backup_stall_timeout`
- **AND** the `GlobalConfig` has `backup_stall_timeout="1h"` and the VM sets no override
- **THEN** `target.backup_stall_timeout == "1h"`

#### Scenario: TargetConfig stall timeout inherits from VM
- **WHEN** a `TargetConfig` is created without explicit `backup_stall_timeout`
- **AND** the VM-level resolved value is `backup_stall_timeout="2h"`
- **THEN** `target.backup_stall_timeout == "2h"`

#### Scenario: TargetConfig stall timeout overrides global
- **WHEN** a `TargetConfig` is created with `backup_stall_timeout="15m"`
- **AND** the `GlobalConfig` has `backup_stall_timeout="30m"`
- **THEN** `target.backup_stall_timeout == "15m"`

### Requirement: convert_parallel field in TargetConfig
`TargetConfig` SHALL include a `convert_parallel: int = 4` field. Valid range is 1-8. Resolution follows global → VM → target inheritance: a target without an explicit value inherits the VM-level resolved value, which itself inherits the global value.

#### Scenario: TargetConfig convert_parallel inherits from global
- **WHEN** a `TargetConfig` is created without explicit `convert_parallel`
- **AND** the `GlobalConfig` has `convert_parallel=2` and the VM sets no override
- **THEN** `target.convert_parallel == 2`

#### Scenario: TargetConfig convert_parallel inherits from VM
- **WHEN** a `TargetConfig` is created without explicit `convert_parallel`
- **AND** the VM-level resolved value is `convert_parallel=8`
- **THEN** `target.convert_parallel == 8`

#### Scenario: TargetConfig convert_parallel overrides global
- **WHEN** a `TargetConfig` is created with `convert_parallel=8`
- **AND** the `GlobalConfig` has `convert_parallel=4`
- **THEN** `target.convert_parallel == 8`

### Requirement: convert_out_of_order field in TargetConfig
`TargetConfig` SHALL include a `convert_out_of_order: bool = True` field. Resolution follows global → VM → target inheritance: a target without an explicit value inherits the VM-level resolved value, which itself inherits the global value.

#### Scenario: TargetConfig convert_out_of_order inherits from global
- **WHEN** a `TargetConfig` is created without explicit `convert_out_of_order`
- **AND** the `GlobalConfig` has `convert_out_of_order=False` and the VM sets no override
- **THEN** `target.convert_out_of_order == False`

#### Scenario: TargetConfig convert_out_of_order inherits from VM
- **WHEN** a `TargetConfig` is created without explicit `convert_out_of_order`
- **AND** the VM-level resolved value is `convert_out_of_order=False`
- **THEN** `target.convert_out_of_order == False`

## ADDED Requirements

### Requirement: VMConfig backup engine option fields
`VMConfig` SHALL include the backup engine option fields `compress: bool = True`, `compression_type: str = "zstd"`, `convert_parallel: int = 4`, `convert_out_of_order: bool = True`, `backup_stall_timeout: str = "30m"`, and `verify: str = "metadata"`. All six fields SHALL be immutable (frozen dataclass). They hold the VM-level resolved values (global default or explicit `[[vm]]` override) and serve as the inheritance fallback for every `TargetConfig` of that VM.

#### Scenario: VMConfig engine option defaults
- **WHEN** a `VMConfig` is created without any engine option values
- **THEN** `compress is True`, `compression_type == "zstd"`, `convert_parallel == 4`, `convert_out_of_order is True`, `backup_stall_timeout == "30m"`, and `verify == "metadata"`

#### Scenario: VMConfig engine options are immutable
- **WHEN** a `VMConfig` is created with `compression_type="zlib"` and `convert_parallel=8`
- **THEN** attempting to mutate either field raises `FrozenInstanceError`

#### Scenario: VMConfig carries explicit VM-level overrides
- **WHEN** a `VMConfig` is created with `compress=False`, `compression_type="zlib"`, `convert_parallel=8`, `convert_out_of_order=False`, `backup_stall_timeout="1h"`, `verify="compare"`
- **THEN** the instance carries exactly those values
