## MODIFIED Requirements

### Requirement: --force-share on check_integrity qemu-img info

`Core.check()` SHALL use `--force-share` on all `qemu-img info` and `qemu-img info --backing-chain` calls that may target active-layer snapshots. This includes the iteration over snapshots from `IStateManager.get_snapshots()` where the most recent snapshot IS the active layer. Additionally, `Core.check()` SHALL parse the JSON output of `qemu-img info --backing-chain --output=json` (not just check exit codes) and verify: (a) every file in the chain exists, (b) every file has format `"qcow2"`, (c) `backing-filename` references are consistent, (d) no cycles.

#### Scenario: check uses --force-share on active layer

- **WHEN** `Core.check()` iterates over snapshots and the most recent is the active layer
- **THEN** `qemu-img info --force-share --backing-chain` is used for that snapshot
- **AND** the command succeeds despite the VM holding a write lock

#### Scenario: check parses JSON and detects inconsistent backing-filename

- **WHEN** `qemu-img info --backing-chain` returns exit code 0
- **AND** the JSON output shows a `backing-filename` that does not match the next file in the chain
- **THEN** `CheckResult(status="broken")` is returned with the inconsistency reported

#### Scenario: check parses JSON and detects cycle

- **WHEN** `qemu-img info --backing-chain` returns exit code 0
- **AND** the JSON output shows a file path appearing twice in the chain
- **THEN** `CheckResult(status="broken")` is returned with "cycle detected at <file>"

### Requirement: --force-share on _deep_check_file qemu-img check

`Core._deep_check_file()` SHALL use `--force-share` on `qemu-img check` when the file being checked may be the active layer. `qemu-img check` is a metadata-only operation (reads headers and refcount tables) and is safe with `--force-share`. The method SHALL check `corruptions`, `errors`, AND `leaks` fields (not just `corruptions`). The timeout SHALL be 7200 seconds (was 60 seconds).

#### Scenario: Deep check on active layer uses --force-share

- **WHEN** `Core._deep_check_file()` is called on a snapshot that is the active layer
- **THEN** `qemu-img check --force-share --output=json` is used
- **AND** the command succeeds despite the VM holding a write lock

#### Scenario: Deep check detects errors (not just corruptions)

- **WHEN** `qemu-img check` reports `errors: 2` (but `corruptions: 0`)
- **THEN** the file is reported as "warning" status
- **AND** the file name is added to the broken list

#### Scenario: Deep check detects leaks

- **WHEN** `qemu-img check` reports `leaks: 5` (but `corruptions: 0` and `errors: 0`)
- **THEN** the file is reported as "warning" status
- **AND** the file name is added to the broken list

#### Scenario: Deep check timeout is 7200 seconds

- **WHEN** `Core._deep_check_file()` runs `qemu-img check`
- **THEN** the timeout parameter is 7200 seconds
- **AND** large disks are not prematurely killed
