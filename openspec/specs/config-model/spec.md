# Configuration Model

## Purpose
Immutable frozen dataclasses representing all qsnap configuration: global defaults, per-VM settings, per-disk base images, and per-target backup destinations.

## Requirements

### Requirement: GlobalConfig default values
The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options, including state directory, lockfile path, count-based retention defaults (`snapshot_chain_length=24`, `target_chain_length=168`, `target_keep_generations=2`), `snapshot_preserve_min=48` (snapshot preservation floor; the newest 48 snapshots per disk are never blockcommitted by default; explicit 0 = inactive), free-space gate controls (`free_space_check="strict"`, `free_space_reserve=0`, `free_space_factor=1.0`), deferred monitoring thresholds, fault-tolerance safety controls, compression default, compression type, convert parallelism, and backup stall timeout.

#### Scenario: GlobalConfig default values
- **WHEN** a `GlobalConfig` is created with only required fields
- **THEN** optional fields have documented defaults: `state_dir="/var/lib/qsnap/state"`, `lockfile=None`, `snapshot_chain_length=24`, `target_chain_length=168`, `target_keep_generations=2`, `snapshot_preserve_min=48`, `free_space_check="strict"`, `free_space_reserve=0`, `free_space_factor=1.0`, `compress=True`, `compression_type="zstd"`, `convert_parallel=4`, `convert_out_of_order=True`, `backup_stall_timeout="30m"`, `auto_cleanup=True`, `state_backup_count=2`, `chain_verify_before_commit=True`, `chain_verify_after_commit=True`, `deep_check_schedule="off"`, `full_verify_after_create="check"`, `full_verify_before_delete="check"`, `transaction_log=None`, `backup_create="always"`

### Requirement: compression_type field in GlobalConfig
`GlobalConfig` SHALL include a `compression_type: str = "zstd"` field. Valid values are `"zstd"` (default) and `"zlib"`. The field is immutable (frozen dataclass).

#### Scenario: GlobalConfig default compression_type is zstd
- **WHEN** a `GlobalConfig` is created with only required fields
- **THEN** `compression_type` defaults to `"zstd"`

#### Scenario: GlobalConfig compression_type is immutable
- **WHEN** a `GlobalConfig` is created with `compression_type="zlib"`
- **THEN** attempting to mutate `compression_type` raises `FrozenInstanceError`

### Requirement: backup_stall_timeout field in GlobalConfig
`GlobalConfig` SHALL include a `backup_stall_timeout: str = "30m"` field. The value is a duration string (e.g. `"30m"`, `"1h"`, `"0s"`) parsed to seconds via `parse_stall_timeout()`. When set to `"0s"`, stall detection is disabled. This field is the global default for all VMs and targets, overridable per-target.

#### Scenario: GlobalConfig default stall timeout is 30m
- **WHEN** a `GlobalConfig` is created with only required fields
- **THEN** `backup_stall_timeout` defaults to `"30m"`

#### Scenario: GlobalConfig stall timeout is immutable
- **WHEN** a `GlobalConfig` is created with `backup_stall_timeout="1h"`
- **THEN** attempting to mutate `backup_stall_timeout` raises `FrozenInstanceError`

### Requirement: DiskConfig dataclass
The system SHALL provide an immutable `DiskConfig` dataclass representing a single disk within a VM. It SHALL have required fields `target: str` (the libvirt device target name, e.g. `"vda"`) and `base_image: Path` (the path to the base qcow2 image for this disk). It SHALL have an optional field `snapshot_dir: Path | None = None` for per-disk snapshot directory override. When `None`, the VM-level `VMConfig.snapshot_dir` is used.

#### Scenario: DiskConfig with required fields
- **WHEN** a `DiskConfig` is created with `target="vda"` and `base_image=Path("/var/lib/libvirt/images/vm.qcow2")`
- **THEN** `disk.target` is `"vda"`, `disk.base_image` is the path, and `disk.snapshot_dir` is `None`

#### Scenario: DiskConfig with per-disk snapshot_dir override
- **WHEN** a `DiskConfig` is created with `target="vdb"`, `base_image=Path("/data/vdb.qcow2")`, `snapshot_dir=Path("/snapshots/vdb")`
- **THEN** `disk.snapshot_dir` is `Path("/snapshots/vdb")`

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

### Requirement: TargetConfig dataclass
The system SHALL provide an immutable `TargetConfig` dataclass representing a backup target. Required field: `path: Path`. Optional fields: `target_chain_length: int | None = None`, `target_keep_generations: int | None = None`, `verify: str = "metadata"`, `compress: bool = True`, `compression_type: str = "zstd"`, `convert_parallel: int = 4`, `convert_out_of_order: bool = True`, `backup_stall_timeout: str = "30m"`, `backup_retry_max: int = 3`, `backup_retry_base: str = "2s"`, `backup_create: str = "always"`. There SHALL be NO `incremental` field.

#### Scenario: TargetConfig with path only — all defaults
- **WHEN** a `TargetConfig` is created with `path=Path("/mnt/backup/myvm")`
- **THEN** `target_chain_length` is `None`, `target_keep_generations` is `None`, `verify` is `"metadata"`, `compress` is `True`, `compression_type` is `"zstd"`, `convert_parallel` is `4`, `convert_out_of_order` is `True`, `backup_stall_timeout` is `"30m"`, `backup_retry_max` is `3`, `backup_retry_base` is `"2s"`, `backup_create` is `"always"`, and there is no `incremental` field

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

### Requirement: GlobalConfig lockfile field is consumed
The `lockfile` field on `GlobalConfig` (default `None`) SHALL be consumed by the locking mechanism. If `lockfile` is not `None`, the process SHALL acquire a lock on this path before pipeline execution.

#### Scenario: Lockfile from config is used
- **WHEN** the config has `lockfile = "/var/lock/qsnap.lock"` and no `--lockfile` CLI flag is passed
- **THEN** a lock is acquired on `/var/lock/qsnap.lock`

### Requirement: TargetConfig verify field
`TargetConfig` SHALL have a `verify: str` field with dataclass-level default `"metadata"`. Valid values SHALL be `"off"` (no verification), `"metadata"` (structural checks), `"compare"` (qemu-img compare chain-traversing content verification). The `"hash"` and `"full"` values are deprecated and treated as `"compare"`.

#### Scenario: Dataclass-level verify default is metadata
- **WHEN** a `TargetConfig` is created with `path=Path("/mnt/backup/myvm")` and no `verify`
- **THEN** `target.verify` is `"metadata"`

#### Scenario: Explicit compare verification
- **WHEN** a `TargetConfig` is created with `verify="compare"`
- **THEN** `target.verify` is `"compare"`

### Requirement: VMConfig snapshot_quiesce field
`VMConfig` SHALL include a `snapshot_quiesce: bool` field with default `False`. When `True`, snapshot creation SHALL request guest-agent filesystem freeze via `--quiesce`.

#### Scenario: Quiesce default is disabled
- **WHEN** a `VMConfig` is created without `snapshot_quiesce`
- **THEN** `vm_config.snapshot_quiesce` is `False`

### Requirement: GlobalConfig deferred threshold fields
`GlobalConfig` SHALL include deferred threshold fields: `deferred_warn_count: str = "5"`, `deferred_crit_count: str = "10"`, `deferred_warn_age: str = "7d"`, `deferred_crit_age: str = "14d"`.

#### Scenario: Deferred threshold defaults

- **WHEN** a `GlobalConfig` is constructed without overrides
- **THEN** `deferred_warn_count == "5"`, `deferred_crit_count == "10"`, `deferred_warn_age == "7d"`, and `deferred_crit_age == "14d"`

### Requirement: GlobalConfig auto_cleanup field
`GlobalConfig` SHALL include an `auto_cleanup: bool` field with default `True`.

#### Scenario: Default auto_cleanup is true
- **WHEN** `GlobalConfig` is constructed without `auto_cleanup`
- **THEN** `auto_cleanup` is `True`

### Requirement: GlobalConfig state_backup_count field
`GlobalConfig` SHALL include a `state_backup_count: int` field with default `2`.

#### Scenario: Default state_backup_count
- **WHEN** `GlobalConfig` is constructed without `state_backup_count`
- **THEN** `state_backup_count` is `2`

### Requirement: GlobalConfig chain verification fields
`GlobalConfig` SHALL include `chain_verify_before_commit: bool` (default `True`) and `chain_verify_after_commit: bool` (default `True`).

#### Scenario: Chain verification enabled by default
- **WHEN** `GlobalConfig` is constructed without chain verify fields
- **THEN** both are `True`

### Requirement: GlobalConfig deep_check_schedule field
`GlobalConfig` SHALL include a `deep_check_schedule: str` field with default `"off"`.

#### Scenario: deep_check_schedule defaults to off
- **WHEN** `GlobalConfig` is constructed without `deep_check_schedule`
- **THEN** `deep_check_schedule` is `"off"`

### Requirement: VMConfig blockcommit_deep_verify field
`VMConfig` SHALL include `blockcommit_deep_verify: bool` with default `False`. There SHALL be NO `snapshot_deep_verify` field on `VMConfig`.

#### Scenario: VMConfig has blockcommit_deep_verify only
- **WHEN** `VMConfig` is constructed
- **THEN** `blockcommit_deep_verify` exists with default `False`
- **AND** `snapshot_deep_verify` does not exist on the dataclass

### Requirement: VMConfig snapshot_create validation
`ConfigFacade._build_vm()` SHALL validate that `snapshot_create` is one of `{"always", "onchange", "ondemand"}`. Invalid values SHALL raise `ConfigError`. The default when not specified SHALL be `"always"`.

#### Scenario: Valid snapshot_create value
- **WHEN** the config has `snapshot_create = "onchange"`
- **THEN** `ConfigFacade` accepts it and stores `"onchange"` in `VMConfig.snapshot_create`

#### Scenario: Invalid snapshot_create value raises ConfigError
- **WHEN** the config has `snapshot_create = "on-changed"`
- **THEN** `ConfigFacade` raises `ConfigError` with a message listing valid values: `"always"`, `"onchange"`, `"ondemand"`

#### Scenario: Default snapshot_create is always
- **WHEN** the config does not specify `snapshot_create`
- **THEN** `VMConfig.snapshot_create` defaults to `"always"`

### Requirement: TargetConfig backup_retry_max and backup_retry_base fields
`TargetConfig` SHALL include `backup_retry_max: int` (default `3`) and `backup_retry_base: str` (default `"2s"`).

#### Scenario: Default retry values
- **WHEN** `TargetConfig` is constructed without retry fields
- **THEN** `backup_retry_max` is `3`, `backup_retry_base` is `"2s"`

### Requirement: TargetConfig compress field
`TargetConfig` SHALL have a `compress: bool` field with default `True`. The field SHALL be immutable (`frozen=True`).

#### Scenario: Default compress is true
- **WHEN** a `TargetConfig` is created without `compress`
- **THEN** `target.compress` is `True`

#### Scenario: Explicit compress disabled
- **WHEN** a `TargetConfig` is created with `compress=False`
- **THEN** `target.compress` is `False`

### Requirement: GlobalConfig compress field
`GlobalConfig` SHALL have a `compress: bool` field with default `True`.

#### Scenario: Global compress default
- **WHEN** `GlobalConfig` is constructed without `compress`
- **THEN** `compress` is `True`

### Requirement: GlobalConfig full_verify_after_create field
`GlobalConfig` SHALL include a `full_verify_after_create: str` field with default `"check"`. Accepted values: `"metadata"` (M1 only), `"check"` (M1+M2), `"compare"` (M1+M2+M3), `"off"` (no verification). The `"hash"` value is deprecated and treated as `"compare"`.

#### Scenario: Default is check
- **WHEN** `full_verify_after_create` is not set
- **THEN** the value is `"check"`

### Requirement: full_verify_before_rebase field REMOVED
There SHALL be NO `full_verify_before_rebase` field on `GlobalConfig`. It was parsed, validated, and stored, but never consumed by any code path.

#### Scenario: full_verify_before_rebase not in GlobalConfig
- **WHEN** `GlobalConfig` is constructed
- **THEN** the dataclass does not have a `full_verify_before_rebase` field

### Requirement: GlobalConfig full_verify_before_delete field
`GlobalConfig` SHALL include a `full_verify_before_delete: str` field with default `"check"`. Controls optional M2 verification before cascade-deletion. M1 is always enforced regardless of this setting.

#### Scenario: Default full_verify_before_delete is check
- **WHEN** `GlobalConfig` is constructed without `full_verify_before_delete`
- **THEN** `full_verify_before_delete` is `"check"`

### Requirement: backup_create field in TargetConfig
`TargetConfig` SHALL include a `backup_create: str = "always"` field. Valid values are `"always"` and `"onchange"`. The field SHALL be immutable (`frozen=True`).

#### Scenario: Default backup_create is always
- **WHEN** a `TargetConfig` is created without `backup_create`
- **THEN** `target.backup_create` is `"always"`

#### Scenario: Explicit onchange mode
- **WHEN** a `TargetConfig` is created with `backup_create="onchange"`
- **THEN** `target.backup_create` is `"onchange"`

### Requirement: backup_create field in GlobalConfig
`GlobalConfig` SHALL include a `backup_create: str = "always"` field.

#### Scenario: Global backup_create default
- **WHEN** `GlobalConfig` is constructed without `backup_create`
- **THEN** `backup_create` is `"always"`

### Requirement: convert_parallel field in GlobalConfig
`GlobalConfig` SHALL include a `convert_parallel: int = 4` field. Valid range is 1-8. The field is immutable.

#### Scenario: GlobalConfig default convert_parallel is 4
- **WHEN** a `GlobalConfig` is created with only required fields
- **THEN** `convert_parallel` defaults to `4`

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

### Requirement: convert_out_of_order field in GlobalConfig
`GlobalConfig` SHALL include a `convert_out_of_order: bool = True` field. The field is immutable.

#### Scenario: GlobalConfig default convert_out_of_order is true
- **WHEN** a `GlobalConfig` is created with only required fields
- **THEN** `convert_out_of_order` defaults to `True`

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

### Requirement: GlobalConfig count-based retention fields
`GlobalConfig` SHALL include `snapshot_chain_length: int | None = 24`, `target_chain_length: int | None = 168`, and `target_keep_generations: int | None = 2`.

#### Scenario: Defaults are 24/168/2
- **WHEN** `GlobalConfig` is constructed without chain_length keys
- **THEN** `snapshot_chain_length` is `24`, `target_chain_length` is `168`, `target_keep_generations` is `2`

#### Scenario: Explicit override still works
- **WHEN** `GlobalConfig` is constructed with `snapshot_chain_length=48`
- **THEN** `snapshot_chain_length` is `48`

### Requirement: VMConfig count-based retention fields
`VMConfig` SHALL include `snapshot_chain_length: int | None = None`, `target_chain_length: int | None = None`, and `target_keep_generations: int | None = None`.

#### Scenario: VM inherits from global
- **WHEN** global sets `snapshot_chain_length = 168` and VM omits it
- **THEN** `VMConfig.snapshot_chain_length` resolves to `168`

### Requirement: TargetConfig count-based retention fields
`TargetConfig` SHALL include `target_chain_length: int | None = None` and `target_keep_generations: int | None = None`.

#### Scenario: Target inherits from VM
- **WHEN** VM sets `target_chain_length = 168` and target omits it
- **THEN** `TargetConfig.target_chain_length` resolves to `168`

#### Scenario: Target overrides VM
- **WHEN** VM sets `target_chain_length = 168` and target sets `target_chain_length = 336`
- **THEN** `TargetConfig.target_chain_length` resolves to `336`

### Requirement: GlobalConfig snapshot_preserve_min field
`GlobalConfig` SHALL include a `snapshot_preserve_min: int = 48` field. The default `48` keeps the newest 48 snapshots of each disk uncommitted (with the default `snapshot_chain_length=24`, the floor dominates effective retention). Setting the field to `0` explicitly disables the preservation floor. The field is immutable.

#### Scenario: GlobalConfig default snapshot_preserve_min is 48
- **WHEN** a `GlobalConfig` is created with only required fields
- **THEN** `snapshot_preserve_min` defaults to `48`

#### Scenario: Explicit zero disables the floor
- **WHEN** a `GlobalConfig` is created with `snapshot_preserve_min=0`
- **THEN** `snapshot_preserve_min` is `0` and the preserve-min filter is inactive

### Requirement: VMConfig snapshot_preserve_min field
`VMConfig` SHALL include a `snapshot_preserve_min: int | None = None` field. When `None`, the value is resolved via option inheritance from `GlobalConfig.snapshot_preserve_min`.

#### Scenario: VM inherits snapshot_preserve_min from global
- **WHEN** global sets `snapshot_preserve_min = 24` and VM omits it
- **THEN** `VMConfig.snapshot_preserve_min` resolves to `24`

#### Scenario: VM overrides global snapshot_preserve_min
- **WHEN** global sets `snapshot_preserve_min = 24` and VM sets `snapshot_preserve_min = 48`
- **THEN** `VMConfig.snapshot_preserve_min` resolves to `48`

### Requirement: GlobalConfig transaction_log field
`GlobalConfig` SHALL include a `transaction_log: str | None = None` field. When `None`, no transaction log is written.

#### Scenario: Default transaction_log is None
- **WHEN** `GlobalConfig` is constructed without `transaction_log`
- **THEN** `transaction_log` is `None`

### Requirement: GlobalConfig free-space gate fields
`GlobalConfig` SHALL include the fields `free_space_check: str = "strict"`, `free_space_reserve: int = 0`, and `free_space_factor: float = 1.0`. `free_space_check` SHALL accept exactly the values `"strict"`, `"warn"`, and `"off"`. `free_space_reserve` is a byte count and SHALL be non-negative. `free_space_factor` SHALL be `>= 1.0`. All three fields are immutable and SHALL be inherited by VMs via the standard option-inheritance mechanism.

#### Scenario: Defaults
- **WHEN** a `GlobalConfig` is created without free-space fields
- **THEN** `free_space_check == "strict"`, `free_space_reserve == 0`, `free_space_factor == 1.0`

#### Scenario: Explicit override
- **WHEN** a `GlobalConfig` is created with `free_space_check="warn"`, `free_space_reserve=1073741824`, `free_space_factor=1.2`
- **THEN** the fields hold exactly those values

#### Scenario: VM inherits free_space_check from global
- **WHEN** global sets `free_space_check = "off"` and the VM omits it
- **THEN** the VM's effective `free_space_check` resolves to `"off"`

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
