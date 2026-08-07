# Configuration Parsing

## Purpose
TOML configuration parsing via `ConfigFacade`, with option inheritance from global → VM → target and validation of all fields.
## Requirements
### Requirement: IConfigFacade ABC
The system SHALL provide an `IConfigFacade` ABC with methods `get_global() → GlobalConfig`, `get_vms() → list[VMConfig]`, and `get_vm(name: str) → VMConfig`.

#### Scenario: ConfigFacade implements IConfigFacade
- **WHEN** `ConfigFacade` is instantiated
- **THEN** `isinstance(facade, IConfigFacade)` is `True`

### Requirement: TOML file parsing
`ConfigFacade` SHALL parse a TOML configuration file and produce immutable `GlobalConfig` and `VMConfig` dataclasses. Global options defined at the top level apply as defaults to all VMs.

#### Scenario: Minimal valid config
- **WHEN** `ConfigFacade` parses a TOML file containing a single `[[vm]]` with required fields (`name`) and at least one `[[vm.disk]]` with `target` and `base_image`
- **THEN** `get_vms()` returns a list with one `VMConfig`, and `get_global()` returns `GlobalConfig` with defaults

#### Scenario: Missing required VM field
- **WHEN** `ConfigFacade` parses a TOML file where `[[vm]]` lacks `name`
- **THEN** a `ConfigError` is raised indicating which field is missing

#### Scenario: Invalid TOML syntax
- **WHEN** `ConfigFacade` attempts to parse a file with malformed TOML
- **THEN** a `ConfigError` is raised with the parse error details

### Requirement: Option inheritance from global to per-VM to per-target
`ConfigFacade` SHALL resolve option inheritance: global-level options are defaults, VM-level options override globals, and target-level options override both. The count-based retention fields (`snapshot_chain_length`, `target_chain_length`, `target_keep_generations`) and the backup engine options (`compress`, `compression_type`, `convert_parallel`, `convert_out_of_order`, `backup_stall_timeout`) SHALL follow the same global → VM → target inheritance chain. The `verify` option SHALL follow VM → target inheritance (it has no global-level key; the default is `"metadata"`).

#### Scenario: VM overrides global chain length
- **WHEN** global config sets `snapshot_chain_length = 168` and a VM sets `snapshot_chain_length = 24`
- **THEN** that VM's `VMConfig` has `snapshot_chain_length = 24`

#### Scenario: Target inherits VM chain length when not overridden
- **WHEN** VM has `target_chain_length = 168` and a target does not specify its own `target_chain_length`
- **THEN** the target's `TargetConfig` has `target_chain_length = 168`

#### Scenario: Target overrides VM chain length
- **WHEN** VM has `target_chain_length = 168` and a target specifies `target_chain_length = 336`
- **THEN** the target's `TargetConfig` has `target_chain_length = 336`

#### Scenario: VM overrides global engine option
- **WHEN** global config sets `compression_type = "zstd"` and a VM sets `compression_type = "zlib"`
- **THEN** that VM's `VMConfig` has `compression_type = "zlib"`

#### Scenario: Target inherits VM engine option when not overridden
- **WHEN** a VM sets `convert_parallel = 8` and its target does not specify `convert_parallel`
- **THEN** the target's `TargetConfig` has `convert_parallel = 8`

#### Scenario: Target overrides VM engine option
- **WHEN** a VM sets `compression_type = "zlib"` and its target sets `compression_type = "zstd"`
- **THEN** the target's `TargetConfig` has `compression_type = "zstd"`

#### Scenario: Target inherits VM verify when not overridden
- **WHEN** a VM sets `verify = "compare"` and its target does not specify `verify`
- **THEN** the target's `TargetConfig` has `verify = "compare"`

### Requirement: Multiple VMs from a single config
`ConfigFacade` SHALL support multiple `[[vm]]` sections in one TOML file, each producing a separate `VMConfig`.

#### Scenario: Config with two VMs
- **WHEN** a TOML file contains `[[vm]]` with `name="vm1"` and another `[[vm]]` with `name="vm2"`
- **THEN** `get_vms()` returns a list of two `VMConfig`s with names `"vm1"` and `"vm2"`

### Requirement: VM lookup by name
`ConfigFacade` SHALL provide `get_vm(name)` that returns the `VMConfig` for a specific VM, or raises an error if not found.

#### Scenario: Lookup existing VM
- **WHEN** `get_vm("vm1")` is called and VM `"vm1"` exists in config
- **THEN** the returned `VMConfig` has `name == "vm1"`

#### Scenario: Lookup non-existent VM
- **WHEN** `get_vm("nonexistent")` is called
- **THEN** a `KeyError` or `ConfigError` is raised

### Requirement: Parsing [[vm.disk]] sections
`ConfigFacade` SHALL parse one or more `[[vm.disk]]` sections within each `[[vm]]` section. Each `[[vm.disk]]` SHALL require `target` (the libvirt device target name, e.g. `"vda"`) and `base_image` (path to the base qcow2 image). An optional `snapshot_dir` field per disk SHALL override the VM-level `snapshot_dir`. A VM MUST define at least one `[[vm.disk]]` section. Disk targets MUST be unique within a VM.

#### Scenario: Single disk with required fields
- **WHEN** a `[[vm]]` contains `[[vm.disk]]` with `target = "vda"` and `base_image = "/var/lib/libvirt/images/vm.qcow2"`
- **THEN** `VMConfig.disks` has one `DiskConfig` with `target="vda"`, `base_image=Path("/var/lib/libvirt/images/vm.qcow2")`, `snapshot_dir=None`

#### Scenario: Multiple disks with optional snapshot_dir overrides
- **WHEN** a `[[vm]]` contains two `[[vm.disk]]` sections, one with `snapshot_dir = "/snaps/vda"` and one without
- **THEN** the first disk has `snapshot_dir=Path("/snaps/vda")` and the second has `snapshot_dir=None`

#### Scenario: Missing disk target raises ConfigError
- **WHEN** a `[[vm.disk]]` section lacks `target`
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating the missing field

#### Scenario: Missing disk base_image raises ConfigError
- **WHEN** a `[[vm.disk]]` section lacks `base_image`
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating the missing field

#### Scenario: Empty disk target raises ConfigError
- **WHEN** a `[[vm.disk]]` section has `target = ""`
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating the target must be non-empty

#### Scenario: Duplicate disk targets raise ConfigError
- **WHEN** a `[[vm]]` contains two `[[vm.disk]]` sections both with `target = "vda"`
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating duplicate target

#### Scenario: No disks raises ConfigError
- **WHEN** a `[[vm]]` has no `[[vm.disk]]` sections
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating at least one `[[vm.disk]]` is required

### Requirement: snapshot_dir resolution at parse time
`ConfigFacade` SHALL verify at parse time that every disk has a resolvable snapshot directory: either its own `snapshot_dir` override or the VM-level `snapshot_dir`. If neither is set for a disk, a `ConfigError` SHALL be raised.

#### Scenario: Disk uses VM-level snapshot_dir
- **WHEN** a `[[vm]]` has `snapshot_dir = "/snaps"` and a `[[vm.disk]]` has no `snapshot_dir`
- **THEN** parsing succeeds and the disk's resolved snapshot directory is `"/snaps"`

#### Scenario: Missing snapshot_dir for a disk raises ConfigError
- **WHEN** a `[[vm]]` has no `snapshot_dir` and a `[[vm.disk]]` has no `snapshot_dir`
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating the disk has no `snapshot_dir`

### Requirement: Distinct snapshot directories per VM
`ConfigFacade` SHALL verify at parse time that no two disks of the same VM resolve to the same snapshot directory. The effective directory of each disk (its own `snapshot_dir` override, or the VM-level default) is normalized via `os.path.normpath` before comparison. If two disks share a directory, a `ConfigError` SHALL be raised naming both disk targets. This guarantees per-disk directory isolation, which child discovery (`QemuImgCommitManager._find_child`) and overlay cleanup rely on.

#### Scenario: Two disks inheriting the same VM-level snapshot_dir rejected
- **WHEN** a `[[vm]]` has `snapshot_dir = "/snaps"` and two `[[vm.disk]]` sections without per-disk overrides
- **THEN** `ConfigFacade` raises `ConfigError` with a message containing "share snapshot_dir"

#### Scenario: Equivalent paths with different spelling rejected
- **WHEN** two disks resolve to `"/snaps/vm"` and `"/snaps/vm/"` respectively
- **THEN** `ConfigFacade` raises `ConfigError` (paths are compared after normalization)

#### Scenario: Distinct per-disk directories accepted
- **WHEN** a `[[vm]]` has `snapshot_dir = "/snaps/vm"` and the second disk overrides with `snapshot_dir = "/snaps/vm-vdb"`
- **THEN** parsing succeeds

### Requirement: ConfigFacade parses fault-tolerance fields
`ConfigFacade` SHALL parse `auto_cleanup`, `state_backup_count`, `chain_verify_before_commit`, `chain_verify_after_commit`, and `deep_check_schedule` from the global section. It SHALL parse `blockcommit_deep_verify` from each `[[vm]]` section. It SHALL parse `backup_retry_max`, `backup_retry_base`, and `compress` from each `[[vm.target]]` section. All fields SHALL use their documented defaults when absent. `ConfigFacade` SHALL NOT parse `full_every` (removed). If `full_every` is present in TOML, a deprecation WARNING SHALL be logged. If `full_compress` is present and `compress` is not, `full_compress` SHALL be mapped to `compress` with a deprecation WARNING. If any of `incremental`, `incremental_mode`, `rate_limit`, or `copy_base` are present in TOML (target-level), a deprecation WARNING SHALL be logged naming the field — the fields are ignored.

#### Scenario: Global safety fields parsed
- **WHEN** config TOML contains `auto_cleanup = true`, `state_backup_count = 2`, `chain_verify_before_commit = true`
- **THEN** `GlobalConfig.auto_cleanup` is `True`, `state_backup_count` is `2`, `chain_verify_before_commit` is `True`

#### Scenario: Target compress parsed
- **WHEN** a `[[vm.target]]` section contains `compress = true`
- **THEN** that target's `TargetConfig.compress` is `True`

#### Scenario: full_every in config triggers deprecation warning
- **WHEN** a `[[vm.target]]` section contains `full_every = "7d"`
- **THEN** a WARNING is logged: `"full_every is deprecated, FULLs are now count-driven"`
- **AND** the value is ignored

#### Scenario: full_compress mapped to compress
- **WHEN** a `[[vm.target]]` section contains `full_compress = true` but no `compress`
- **THEN** `TargetConfig.compress` is `True`
- **AND** a WARNING is logged: `"full_compress is deprecated — use 'compress' instead"`

#### Scenario: VM deep verify fields parsed
- **WHEN** a `[[vm]]` section contains `blockcommit_deep_verify = true`
- **THEN** that VM's `VMConfig.blockcommit_deep_verify` is `True`

#### Scenario: Target retry fields parsed
- **WHEN** a `[[vm.target]]` section contains `backup_retry_max = 5` and `backup_retry_base = "10s"`
- **THEN** that target's `TargetConfig.backup_retry_max` is `5` and `backup_retry_base` is `"10s"`

### Requirement: Parse compression_type from TOML
`ConfigFacade` SHALL parse the `compression_type` field from the TOML config file at the global, VM, and target levels. The field SHALL be validated against the set `{"zstd", "zlib"}` at every level. If the value is not in this set, a `ConfigError` SHALL be raised naming the offending level (and VM, when applicable). If absent from TOML at all levels, the default `"zstd"` SHALL be used.

#### Scenario: Global compression_type parsed from TOML
- **WHEN** the TOML config contains `[global] compression_type = "zlib"`
- **THEN** `GlobalConfig.compression_type == "zlib"`

#### Scenario: VM-level compression_type parsed from TOML
- **WHEN** the TOML config contains `compression_type = "zlib"` inside a `[[vm]]` section
- **THEN** that VM's `VMConfig.compression_type == "zlib"`

#### Scenario: Target compression_type overrides VM
- **WHEN** the TOML config contains `[[vm]] compression_type = "zlib"` and `[[vm.target]] compression_type = "zstd"`
- **THEN** the target's `compression_type == "zstd"`

#### Scenario: Target compression_type overrides global
- **WHEN** the TOML config contains `[global] compression_type = "zstd"` and `[[vm.target]] compression_type = "zlib"`
- **THEN** the target's `compression_type == "zlib"`

#### Scenario: Invalid compression_type raises ConfigError
- **WHEN** the TOML config contains `compression_type = "lz4"`
- **THEN** a `ConfigError` is raised with message indicating valid values

### Requirement: Parse backup_stall_timeout from TOML
`ConfigFacade` SHALL parse the `backup_stall_timeout` field from the TOML config at the global, VM, and target levels. The value SHALL be a duration string validated via `parse_stall_timeout()` at every level. If invalid, a `ConfigError` SHALL be raised. If absent at all levels, the default `"30m"` SHALL be used.

#### Scenario: Global backup_stall_timeout parsed from TOML
- **WHEN** the TOML config contains `[global] backup_stall_timeout = "1h"`
- **THEN** `GlobalConfig.backup_stall_timeout == "1h"`

#### Scenario: VM-level backup_stall_timeout parsed from TOML
- **WHEN** the TOML config contains `backup_stall_timeout = "45m"` inside a `[[vm]]` section
- **THEN** that VM's `VMConfig.backup_stall_timeout == "45m"`

#### Scenario: Target backup_stall_timeout overrides global
- **WHEN** the TOML config contains `[global] backup_stall_timeout = "30m"` and `[[vm.target]] backup_stall_timeout = "15m"`
- **THEN** the target's `backup_stall_timeout == "15m"`

#### Scenario: Target backup_stall_timeout inherits VM value
- **WHEN** the TOML config contains `[[vm]] backup_stall_timeout = "2h"` and the target does not set `backup_stall_timeout`
- **THEN** the target's `backup_stall_timeout == "2h"`

#### Scenario: Invalid backup_stall_timeout raises ConfigError
- **WHEN** the TOML config contains `backup_stall_timeout = "abc"`
- **THEN** a `ConfigError` is raised with message indicating invalid duration format

### Requirement: backup_create option resolution
`ConfigFacade` SHALL resolve the `backup_create` option via inheritance: global → VM → target. The VM level SHALL inherit from `GlobalConfig.backup_create` when not set in the VM's TOML section. The target level SHALL inherit from the VM-level resolved value when not set in the target's TOML section. `ConfigFacade` SHALL validate that `backup_create` is one of `{"always", "onchange"}`. Invalid values SHALL raise `ConfigError`.

#### Scenario: Valid backup_create value
- **WHEN** the config has `backup_create = "onchange"` at the target level
- **THEN** `ConfigFacade` accepts it and stores `"onchange"` in `TargetConfig.backup_create`

#### Scenario: Invalid backup_create raises ConfigError
- **WHEN** the config has `backup_create = "on-change"` at any level
- **THEN** `ConfigFacade` raises `ConfigError` with a message listing valid values

#### Scenario: Global backup_create inherited by target
- **WHEN** global config sets `backup_create = "onchange"` and neither VM nor target specify it
- **THEN** `TargetConfig.backup_create` resolves to `"onchange"`

#### Scenario: Target-level backup_create overrides VM
- **WHEN** VM sets `backup_create = "onchange"` and target sets `backup_create = "always"`
- **THEN** `TargetConfig.backup_create` resolves to `"always"`

### Requirement: [global] TOML section support
`ConfigFacade._parse()` SHALL accept a `[global]` section in the TOML config file. When a `[global]` section is present, its keys SHALL be unwrapped to the top level before resolving global options. If both a `[global]` section and top-level keys exist, the top-level keys SHALL take precedence.

#### Scenario: [global] section keys parsed correctly
- **WHEN** the TOML config contains `[global]` with `compress = false` and `lockfile = "/run/qsnap.lock"`
- **THEN** `GlobalConfig.compress` is `False` and `GlobalConfig.lockfile` is `"/run/qsnap.lock"`

#### Scenario: Top-level keys override [global] section
- **WHEN** the TOML config contains both `compress = true` at the top level and `[global] compress = false`
- **THEN** `GlobalConfig.compress` is `True`

#### Scenario: No [global] section — backward compatible
- **WHEN** the TOML config uses top-level keys only (no `[global]` section)
- **THEN** parsing works exactly as before

### Requirement: ConfigFacade parses count-based retention fields
`ConfigFacade` SHALL parse `snapshot_chain_length` (int), `target_chain_length` (int), and `target_keep_generations` (int) from the global, VM, and target TOML sections. These fields SHALL use option inheritance (global → VM → target). When absent at all levels, they SHALL default to `None`.

#### Scenario: Global chain_length parsed
- **WHEN** config TOML contains `snapshot_chain_length = 168` at the global level
- **THEN** `GlobalConfig.snapshot_chain_length` is `168`

#### Scenario: VM-level chain_length overrides global
- **WHEN** global sets `snapshot_chain_length = 168` and a VM sets `snapshot_chain_length = 24`
- **THEN** `VMConfig.snapshot_chain_length` is `24`

### Requirement: Count-based retention validation
`ConfigFacade` SHALL validate that `snapshot_chain_length` (when set) is an integer >= 1. `ConfigFacade` SHALL validate that `target_chain_length` (when set) is an integer >= 1. `ConfigFacade` SHALL validate that `target_keep_generations` (when set) is an integer >= 1. Values of 0 or negative SHALL raise `ConfigError`.

#### Scenario: Valid chain_length
- **WHEN** config has `snapshot_chain_length = 168`
- **THEN** `ConfigFacade` accepts it

#### Scenario: Zero chain_length rejected
- **WHEN** config has `snapshot_chain_length = 0`
- **THEN** `ConfigFacade` raises `ConfigError` with message: `"snapshot_chain_length must be >= 1"`

#### Scenario: Negative keep_generations rejected
- **WHEN** config has `target_keep_generations = -1`
- **THEN** `ConfigFacade` raises `ConfigError` with message: `"target_keep_generations must be >= 1"`

### Requirement: snapshot_preserve_min validation
`ConfigFacade` SHALL validate that `snapshot_preserve_min` is a non-negative integer. Negative values SHALL raise `ConfigError`.

#### Scenario: Valid snapshot_preserve_min value
- **WHEN** the config has `snapshot_preserve_min = 24`
- **THEN** `ConfigFacade` accepts it and stores `24` in `VMConfig.snapshot_preserve_min`

#### Scenario: Negative snapshot_preserve_min raises ConfigError
- **WHEN** the config has `snapshot_preserve_min = -1`
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating the valid range is >= 0

### Requirement: convert_parallel validation
`ConfigFacade` SHALL validate that `convert_parallel` is an integer in the range 1-8 at every level where it is parsed (global, VM, and target). Values outside this range SHALL raise `ConfigError`.

#### Scenario: Valid convert_parallel value
- **WHEN** the config has `convert_parallel = 2`
- **THEN** `ConfigFacade` accepts it

#### Scenario: VM-level convert_parallel accepted
- **WHEN** a `[[vm]]` section sets `convert_parallel = 8`
- **THEN** `ConfigFacade` accepts it and `VMConfig.convert_parallel == 8`

#### Scenario: convert_parallel below range raises ConfigError
- **WHEN** the config has `convert_parallel = 0`
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating the valid range is 1-8

#### Scenario: VM-level convert_parallel above range raises ConfigError
- **WHEN** a `[[vm]]` section sets `convert_parallel = 9`
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating the valid range is 1-8 and naming the VM

### Requirement: backup_retry_base validation
`ConfigFacade` SHALL validate that `backup_retry_base` matches the pattern `^\d+s$` (a positive integer followed by `"s"`). Invalid values SHALL raise `ConfigError`.

#### Scenario: Valid backup_retry_base
- **WHEN** the config has `backup_retry_base = "10s"`
- **THEN** `ConfigFacade` accepts it

#### Scenario: Invalid backup_retry_base raises ConfigError
- **WHEN** the config has `backup_retry_base = "abc"`
- **THEN** `ConfigFacade` raises `ConfigError` with a message indicating the format must be like `"1s"`, `"5s"`, `"10s"`

### Requirement: TargetConfig verify parsing
`ConfigFacade` SHALL parse the `verify` field on each `[[vm.target]]`. Valid explicit values are `"off"`, `"metadata"`, `"check"`, `"compare"`. If absent on the target, the value SHALL be inherited from the VM-level resolved `verify` (which itself defaults to `"metadata"` when absent on the VM). The deprecated values `"hash"` and `"full"` SHALL be treated as `"compare"` with a WARNING at whichever level they appear.

#### Scenario: Explicit verify value
- **WHEN** config has `verify = "compare"`
- **THEN** `TargetConfig.verify` is `"compare"`

#### Scenario: Deprecated hash treated as compare
- **WHEN** config has `verify = "hash"`
- **THEN** a WARNING is logged and `TargetConfig.verify` resolves to `"compare"`

#### Scenario: Verify absent defaults to metadata
- **WHEN** neither the VM nor the target specifies `verify`
- **THEN** `TargetConfig.verify` is `"metadata"`

#### Scenario: Target verify inherits VM value
- **WHEN** a `[[vm]]` section sets `verify = "check"` and its target does not set `verify`
- **THEN** `TargetConfig.verify` is `"check"`

#### Scenario: Target verify overrides VM value
- **WHEN** a `[[vm]]` section sets `verify = "check"` and its target sets `verify = "off"`
- **THEN** `TargetConfig.verify` is `"off"`

### Requirement: full_verify_after_create validation
`ConfigFacade` SHALL validate `full_verify_after_create` against `{"metadata", "check", "compare", "off"}`. The deprecated `"hash"` value SHALL be remapped to `"compare"` with a WARNING.

#### Scenario: Invalid value raises ConfigError
- **WHEN** config has `full_verify_after_create = "none"`
- **THEN** `ConfigFacade` raises `ConfigError`

### Requirement: full_verify_before_delete validation
`ConfigFacade` SHALL validate `full_verify_before_delete` against `{"metadata", "check", "off"}`.

#### Scenario: Invalid value raises ConfigError
- **WHEN** config has `full_verify_before_delete = "none"`
- **THEN** `ConfigFacade` raises `ConfigError`

### Requirement: deep_check_schedule validation
`ConfigFacade` SHALL validate `deep_check_schedule` against `{"off", "weekly", "monthly"}`.

#### Scenario: Invalid value raises ConfigError
- **WHEN** config has `deep_check_schedule = "daily"`
- **THEN** `ConfigFacade` raises `ConfigError`

### Requirement: TOML with deprecated incremental key logs deprecation WARNING
`ConfigFacade` SHALL log a deprecation WARNING when the deprecated `incremental` key appears under `[[vm.target]]` and ignore the value.

#### Scenario: TOML with incremental key logs deprecation WARNING
- **WHEN** a TOML config contains `incremental = false` under `[[vm.target]]`
- **THEN** `ConfigFacade` logs a WARNING: `"incremental is deprecated and ignored — all backups are now bitmap-based"`
- **AND** `TargetConfig` is created without the `incremental` field
- **AND** no `ConfigError` is raised

### Requirement: change_detection_mode parse-time default
`ConfigFacade` SHALL parse the optional `[[vm]]` key `change_detection_mode`. When the key is absent, the parsed `VMConfig.change_detection_mode` SHALL equal the spec default `"allocation-map"` (matching the `VMConfig` dataclass default). When the key is present, its string value SHALL be passed through unchanged.

#### Scenario: Absent key parses to allocation-map default
- **WHEN** `ConfigFacade` parses a TOML file whose `[[vm]]` section does not set `change_detection_mode`
- **THEN** the resulting `VMConfig.change_detection_mode` equals `"allocation-map"`

#### Scenario: Explicit allocation-size is preserved
- **WHEN** `ConfigFacade` parses a TOML file whose `[[vm]]` section sets `change_detection_mode = "allocation-size"`
- **THEN** the resulting `VMConfig.change_detection_mode` equals `"allocation-size"`

### Requirement: ConfigFacade parses and validates free-space gate fields
`ConfigFacade` SHALL parse `free_space_check`, `free_space_reserve`, and `free_space_factor` from the global section (and honor the standard global→VM inheritance for them). Validation SHALL enforce: `free_space_check` is one of `"strict"`, `"warn"`, `"off"` — any other value SHALL raise `ConfigError` naming the valid values; `free_space_reserve` is a non-negative integer — negative values SHALL raise `ConfigError`; `free_space_factor` is a number `>= 1.0` — smaller values SHALL raise `ConfigError`. Absent fields SHALL use the documented defaults (`"strict"`, `0`, `1.0`).

#### Scenario: Valid free-space fields parsed
- **WHEN** the global section contains `free_space_check = "warn"`, `free_space_reserve = 1073741824`, `free_space_factor = 1.1`
- **THEN** `GlobalConfig.free_space_check` is `"warn"`, `free_space_reserve` is `1073741824`, `free_space_factor` is `1.1`

#### Scenario: Invalid free_space_check raises ConfigError
- **WHEN** the config has `free_space_check = "hard"`
- **THEN** `ConfigFacade` raises `ConfigError` naming `strict`, `warn`, `off` as valid values

#### Scenario: Negative free_space_reserve raises ConfigError
- **WHEN** the config has `free_space_reserve = -1`
- **THEN** `ConfigFacade` raises `ConfigError` indicating the value must be >= 0

#### Scenario: free_space_factor below 1.0 raises ConfigError
- **WHEN** the config has `free_space_factor = 0.5`
- **THEN** `ConfigFacade` raises `ConfigError` indicating the value must be >= 1.0

#### Scenario: Absent fields use defaults
- **WHEN** the config omits all three free-space fields
- **THEN** `free_space_check == "strict"`, `free_space_reserve == 0`, `free_space_factor == 1.0`

#### Scenario: snapshot_preserve_min default resolves to 48
- **WHEN** the config omits `snapshot_preserve_min` at all levels
- **THEN** `VMConfig.snapshot_preserve_min` resolves to `48` via inheritance from the global default

#### Scenario: Explicit zero preserve_min still honored
- **WHEN** the global section sets `snapshot_preserve_min = 0` and the VM omits it
- **THEN** `VMConfig.snapshot_preserve_min` resolves to `0` (floor inactive)

### Requirement: VM-level backup engine option parsing
`ConfigFacade` SHALL parse the backup engine options `compress` (bool), `compression_type` (str), `convert_parallel` (int), `convert_out_of_order` (bool), `backup_stall_timeout` (duration string), and `verify` (str) from each `[[vm]]` section. Each parsed value SHALL be stored on the corresponding `VMConfig` field. When a key is absent from the `[[vm]]` section, the VM-level value SHALL be inherited from `GlobalConfig` (`verify` inherits the default `"metadata"`, having no global key). VM-level values SHALL undergo the same validation as the corresponding global/target fields (`compression_type` in `{"zstd", "zlib"}`, `convert_parallel` in 1-8, `verify` in `{"off", "metadata", "check", "compare"}` plus deprecated-value mapping, `backup_stall_timeout` via `parse_stall_timeout()`). Validation failures SHALL raise `ConfigError` with a message naming the VM.

#### Scenario: All six engine options parsed at VM level
- **WHEN** a `[[vm]]` section sets `compress = false`, `compression_type = "zlib"`, `convert_parallel = 8`, `convert_out_of_order = false`, `backup_stall_timeout = "1h"`, and `verify = "compare"`
- **THEN** the resulting `VMConfig` carries exactly those six values

#### Scenario: Absent VM-level options inherit global values
- **WHEN** the global section sets `compress = false`, `compression_type = "zlib"`, `convert_parallel = 2`, `convert_out_of_order = false`, `backup_stall_timeout = "1h"` and a `[[vm]]` section sets none of them
- **THEN** the resulting `VMConfig` carries `compress=False`, `compression_type="zlib"`, `convert_parallel=2`, `convert_out_of_order=False`, `backup_stall_timeout="1h"`, and `verify="metadata"`

#### Scenario: VM-level options feed target resolution
- **WHEN** a `[[vm]]` section sets `compression_type = "zlib"` and `convert_parallel = 8` and its `[[vm.target]]` sets neither
- **THEN** the resulting `TargetConfig` has `compression_type == "zlib"` and `convert_parallel == 8`

#### Scenario: Invalid VM-level compression_type raises ConfigError naming the VM
- **WHEN** a `[[vm]]` section with `name = "web01"` sets `compression_type = "lz4"`
- **THEN** `ConfigFacade` raises `ConfigError` with a message containing the VM name and the valid values

### Requirement: Unknown config key rejection
`ConfigFacade` SHALL reject any key that is not recognized at its table level. Recognition is defined by per-level whitelists covering: the top-level/`[global]` table, each `[[vm]]` table, each `[[vm.disk]]` table, and each `[[vm.target]]` table. When an unknown key is found, `ConfigFacade` SHALL raise `ConfigError` with a message naming the table (including the VM name and target path where applicable) and the offending key. When the unknown key is a recognized key at a different level, the message SHALL include a hint pointing at the correct level. Deprecated-but-tolerated keys (keys that today trigger a deprecation WARNING and are ignored or mapped) SHALL remain accepted and SHALL NOT raise. Structural keys (`vm` at top level; `disk` and `target` inside `[[vm]]`) SHALL be accepted. `[global]` section keys are unwrapped to the top level before validation.

#### Scenario: Unknown key at VM level raises ConfigError
- **WHEN** a `[[vm]]` section with `name = "web01"` contains `compresion_type = "zlib"` (typo)
- **THEN** `ConfigFacade` raises `ConfigError` with a message naming the `[[vm]]` table, the VM name, and the key `compresion_type`

#### Scenario: Unknown key at target level raises ConfigError
- **WHEN** a `[[vm.target]]` section contains `parallel = 8`
- **THEN** `ConfigFacade` raises `ConfigError` with a message naming the key `parallel`

#### Scenario: Unknown key at global level raises ConfigError
- **WHEN** the top-level config contains `compresss = true`
- **THEN** `ConfigFacade` raises `ConfigError` with a message naming the key `compresss`

#### Scenario: Unknown key at disk level raises ConfigError
- **WHEN** a `[[vm.disk]]` section contains `base = "/images/vm.qcow2"`
- **THEN** `ConfigFacade` raises `ConfigError` with a message naming the key `base`

#### Scenario: Hint when key belongs to another level
- **WHEN** a `[[vm]]` section contains a key that is recognized only at the target level (e.g. `backup_retry_max`)
- **THEN** the `ConfigError` message includes a hint that the key belongs in `[[vm.target]]`

#### Scenario: Deprecated keys remain tolerated
- **WHEN** the global section contains `rate_limit = "10M"` or any deprecated retention key (`snapshot_preserve`, `target_preserve`, `target_preserve_min`, `preserve_day_of_week`)
- **THEN** no `ConfigError` is raised and the existing deprecation WARNING behavior is preserved

#### Scenario: All fixture configs still parse
- **WHEN** `ConfigFacade` parses every TOML fixture under `tests/fixtures/configs/`
- **THEN** no `ConfigError` about unknown keys is raised

