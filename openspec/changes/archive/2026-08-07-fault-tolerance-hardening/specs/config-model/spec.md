# Config Model — Delta

## MODIFIED Requirements

### Requirement: GlobalConfig default values
The system SHALL provide an immutable `GlobalConfig` dataclass with frozen fields representing global configuration options, including state directory, lockfile path, count-based retention defaults (`snapshot_chain_length=24`, `target_chain_length=168`, `target_keep_generations=2`), `snapshot_preserve_min=48` (snapshot preservation floor; the newest 48 snapshots per disk are never blockcommitted by default; explicit 0 = inactive), free-space gate controls (`free_space_check="strict"`, `free_space_reserve=0`, `free_space_factor=1.0`), deferred monitoring thresholds, fault-tolerance safety controls, compression default, compression type, convert parallelism, and backup stall timeout.

#### Scenario: GlobalConfig default values
- **WHEN** a `GlobalConfig` is created with only required fields
- **THEN** optional fields have documented defaults: `state_dir="/var/lib/qsnap/state"`, `lockfile=None`, `snapshot_chain_length=24`, `target_chain_length=168`, `target_keep_generations=2`, `snapshot_preserve_min=48`, `free_space_check="strict"`, `free_space_reserve=0`, `free_space_factor=1.0`, `compress=True`, `compression_type="zstd"`, `convert_parallel=4`, `convert_out_of_order=True`, `backup_stall_timeout="30m"`, `auto_cleanup=True`, `state_backup_count=2`, `chain_verify_before_commit=True`, `chain_verify_after_commit=True`, `deep_check_schedule="off"`, `full_verify_after_create="check"`, `full_verify_before_delete="check"`, `transaction_log=None`, `backup_create="always"`

### Requirement: GlobalConfig snapshot_preserve_min field
`GlobalConfig` SHALL include a `snapshot_preserve_min: int = 48` field. The default `48`
keeps the newest 48 snapshots of each disk uncommitted (with the default
`snapshot_chain_length=24`, the floor dominates effective retention). Setting the field
to `0` explicitly disables the preservation floor. The field is immutable.

#### Scenario: GlobalConfig default snapshot_preserve_min is 48
- **WHEN** a `GlobalConfig` is created with only required fields
- **THEN** `snapshot_preserve_min` defaults to `48`

#### Scenario: Explicit zero disables the floor
- **WHEN** a `GlobalConfig` is created with `snapshot_preserve_min=0`
- **THEN** `snapshot_preserve_min` is `0` and the preserve-min filter is inactive

## ADDED Requirements

### Requirement: GlobalConfig free-space gate fields
`GlobalConfig` SHALL include the fields `free_space_check: str = "strict"`,
`free_space_reserve: int = 0`, and `free_space_factor: float = 1.0`.
`free_space_check` SHALL accept exactly the values `"strict"`, `"warn"`, and `"off"`.
`free_space_reserve` is a byte count and SHALL be non-negative. `free_space_factor`
SHALL be `>= 1.0`. All three fields are immutable and SHALL be inherited by VMs via the
standard option-inheritance mechanism.

#### Scenario: Defaults
- **WHEN** a `GlobalConfig` is created without free-space fields
- **THEN** `free_space_check == "strict"`, `free_space_reserve == 0`,
  `free_space_factor == 1.0`

#### Scenario: Explicit override
- **WHEN** a `GlobalConfig` is created with `free_space_check="warn"`,
  `free_space_reserve=1073741824`, `free_space_factor=1.2`
- **THEN** the fields hold exactly those values

#### Scenario: VM inherits free_space_check from global
- **WHEN** global sets `free_space_check = "off"` and the VM omits it
- **THEN** the VM's effective `free_space_check` resolves to `"off"`
