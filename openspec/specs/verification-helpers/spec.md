# Verification Helpers

## Purpose

Shared verification utility functions extracted from duplicated code in lifecycle managers and Core. Consolidates 4 independent backing-chain verification implementations and 2 identical deep-verify blocks into reusable, testable functions.

## ADDED Requirements

### Requirement: Shared deep_verify_base_image function

The system SHALL provide a `deep_verify_base_image(shell: IShell, base_image: Path) -> CommitResult | None` function in `qsnap/utils/verification.py`. When called, it SHALL run `qemu-img check --output=json` on `base_image` with a 3600-second timeout. On success, it SHALL parse the JSON output and check `corruptions`, `errors`, and `leaks` fields. If any field is non-zero, it SHALL return `CommitResult(success=False, committed_snapshot="", error="deep verify: {count} {field} in base image")`. If `qemu-img check` fails (non-zero exit, `chk.success is False`), it SHALL return `CommitResult(success=False, committed_snapshot="", error="deep verify: qemu-img check failed: {chk.error}")`. If JSON parsing fails, it SHALL return `CommitResult(success=False, committed_snapshot="", error="deep verify: failed to parse qemu-img check output")`. If the check passes with zero corruptions/errors/leaks, it SHALL return `None`.

The `shell.run()` call SHALL NOT pass `check=True` — the function inspects `chk.success` and `chk.error` directly, consistent with the shell abstraction contract (result objects, not exceptions for expected failures).

#### Scenario: deep_verify passes — no corruption

- **WHEN** `deep_verify_base_image(shell, base_image)` is called
- **AND** `qemu-img check` returns exit 0 with `{"corruptions": 0, "errors": 0, "leaks": 0}`
- **THEN** the function returns `None` (pass)

#### Scenario: deep_verify fails with corruptions

- **WHEN** `deep_verify_base_image(shell, base_image)` is called
- **AND** `qemu-img check` returns `{"corruptions": 5, "errors": 0, "leaks": 0}`
- **THEN** the function returns `CommitResult(success=False, error="deep verify: 5 corruptions in base image")`

#### Scenario: deep_verify fails with errors

- **WHEN** `deep_verify_base_image(shell, base_image)` is called
- **AND** `qemu-img check` returns `{"corruptions": 0, "errors": 2, "leaks": 0}`
- **THEN** the function returns `CommitResult(success=False, error="deep verify: 2 errors in base image")`

#### Scenario: qemu-img check command fails

- **WHEN** `deep_verify_base_image(shell, base_image)` is called
- **AND** `qemu-img check` exits with non-zero and `chk.success is False`
- **THEN** the function returns `CommitResult(success=False, error="deep verify: qemu-img check failed: {chk.error}")`

#### Scenario: JSON parsing fails

- **WHEN** `deep_verify_base_image(shell, base_image)` is called
- **AND** `qemu-img check` returns invalid JSON
- **THEN** the function returns `CommitResult(success=False, error="deep verify: failed to parse qemu-img check output")`

### Requirement: Shared scan_backing_chain function

The system SHALL provide a `scan_backing_chain(shell: IShell, entry_path: Path) -> ChainScanResult` function in `qsnap/utils/verification.py`. It SHALL run `qemu-img info --force-share --backing-chain --output=json` on `entry_path` with a 30-second timeout. It SHALL parse the JSON output and verify: (a) every file referenced in the chain exists on the filesystem, (b) every file has format `"qcow2"`, (c) the `backing-filename` reference in each image matches the actual next file in the chain, (d) no file appears twice (no cycles). All paths from the chain SHALL be collected in `ChainScanResult.paths`. Files with issues (missing, non-qcow2 format, cycle, backing-filename mismatch) SHALL be added to `ChainScanResult.broken_files`. If the `qemu-img info` command fails or JSON parsing fails, `ChainScanResult.success` SHALL be `False` and `ChainScanResult.error` SHALL contain the failure reason.

The JSON parsing SHALL accept both `"image"` (legacy QEMU) and `"filename"` (QEMU 11.0+) as the key for the disk image file path. The `"children"` nested array SHALL be ignored.

`ChainScanResult` SHALL be a frozen dataclass with fields: `paths: set[str]`, `broken_files: list[str]`, `success: bool`, `error: str | None`.

#### Scenario: Intact chain — all checks pass

- **WHEN** `scan_backing_chain(shell, entry_path)` is called on a 5-file qcow2 chain
- **AND** all files exist, all are qcow2, references consistent, no cycles
- **THEN** `ChainScanResult.success` is `True`
- **AND** `ChainScanResult.paths` contains all 5 file paths
- **AND** `ChainScanResult.broken_files` is empty

#### Scenario: Missing file in chain

- **WHEN** `scan_backing_chain(shell, entry_path)` is called
- **AND** one file in the chain does not exist on disk
- **THEN** `ChainScanResult.broken_files` contains the path of the missing file
- **AND** `ChainScanResult.success` is `True` (detection succeeded, chain has issues)

#### Scenario: Non-qcow2 file in chain

- **WHEN** `scan_backing_chain(shell, entry_path)` is called
- **AND** one file has `format: "raw"`
- **THEN** `ChainScanResult.broken_files` contains the file path
- **AND** file existence check still passes for that file

#### Scenario: qemu-img info command fails

- **WHEN** `scan_backing_chain(shell, entry_path)` is called
- **AND** `qemu-img info --backing-chain` exits with non-zero
- **THEN** `ChainScanResult.success` is `False`
- **AND** `ChainScanResult.error` contains the failure reason

### Requirement: Both BlockCommitManager and QemuImgCommitManager use deep_verify_base_image

Both `BlockCommitManager.blockcommit()` and `QemuImgCommitManager.blockcommit()` SHALL replace their inline `qemu-img check` implementations with a call to `deep_verify_base_image()`. When `deep_verify=True`, each manager SHALL call `fail = deep_verify_base_image(self._shell, vm_config.base_image)`. If the result is not `None`, the manager SHALL return it immediately as the `CommitResult`. This replaces the previous ~44-line duplicated block in each manager.

#### Scenario: BlockCommitManager uses shared deep_verify

- **WHEN** `BlockCommitManager.blockcommit(vm_config, snapshots, deep_verify=True)` is called
- **AND** the commit succeeds
- **THEN** `deep_verify_base_image(self._shell, vm_config.base_image)` is called
- **AND** the return value (CommitResult or None) determines the final result

#### Scenario: QemuImgCommitManager uses shared deep_verify

- **WHEN** `QemuImgCommitManager.blockcommit(vm_config, snapshots, deep_verify=True)` is called
- **AND** the commit succeeds
- **THEN** `deep_verify_base_image(self._shell, vm_config.base_image)` is called
- **AND** the return value (CommitResult or None) determines the final result

### Requirement: All chain verification uses scan_backing_chain

`Core._verify_backing_chain()`, `Core._check_snapshot_chain()`, `Core._check_target_consistency()`, and the post-cleanup path in `Core._cleanup_backups()` SHALL use `scan_backing_chain()` as their backing-chain verification engine. Each call site SHALL extract from `ChainScanResult` the fields it needs:

- `_verify_backing_chain()` SHALL convert `ChainScanResult` → `ChainVerifyResult`, mapping `broken_files[0]` to `broken_file` if any.
- `_check_snapshot_chain()` SHALL use `ChainScanResult.paths` as the return value and `ChainScanResult.broken_files` to populate the `broken: list[str]` side-effect parameter.
- `_check_target_consistency()` and post-cleanup SHALL check `ChainScanResult.success` and log CRITICAL on failure.

#### Scenario: _verify_backing_chain uses scan_backing_chain

- **WHEN** `_verify_backing_chain(vm_config)` is called
- **THEN** it calls `scan_backing_chain(self._shell, active_path)`
- **AND** converts `ChainScanResult` to `ChainVerifyResult` with `broken_file` set from `broken_files[0]` if non-empty

#### Scenario: _check_snapshot_chain uses scan_backing_chain

- **WHEN** `_check_snapshot_chain(vm, broken)` is called
- **THEN** it calls `scan_backing_chain(self._shell, active_layer)`
- **AND** returns `result.paths` and appends `result.broken_files` items to the `broken` list
