## REMOVED Requirements

### Requirement: M1 metadata verification of FULL before rebase
**Reason**: The `full_verify_before_rebase` config field was never wired into Core — it was parsed, validated, and stored in `GlobalConfig`, but zero code paths consumed it. The rebase step it was intended to protect died with `FileCopyBackupProvider` (removed in `2026-07-22-remove-rsync-filecopy`). The `BitmapBackupProvider` does not rebase incrementals to FULL anchors — it creates backing-chained COW deltas via `qemu-img create -b`.
**Migration**: Remove `full_verify_before_rebase` from TOML configs. The field is silently ignored if present (unknown key warning). No runtime behavior changes because the field was never consumed.

## MODIFIED Requirements

### Requirement: M2 structural verification of FULL (qemu-img check)

When configured (`GlobalConfig.full_verify_after_create = "check"` or `"compare"`, or `full_verify_before_delete = "check"`), Core SHALL additionally run `qemu-img check --output=json` and verify that ALL of `errors`, `leaks`, AND `corruptions` are zero. Any non-zero value among the three fields SHALL fail verification.

#### Scenario: M2 passes when all fields are zero

- **WHEN** `qemu-img check --output=json` returns `{"errors": 0, "leaks": 0, "corruptions": 0}`
- **THEN** M2 verification passes

#### Scenario: M2 fails on non-zero corruptions

- **WHEN** `qemu-img check --output=json` returns `{"errors": 0, "leaks": 0, "corruptions": 3}`
- **THEN** M2 verification fails with an error message naming the corruption count

#### Scenario: M2 fails on non-zero errors

- **WHEN** `qemu-img check --output=json` returns `{"errors": 2, "leaks": 0, "corruptions": 0}`
- **THEN** M2 verification fails with an error message naming the error count

#### Scenario: M2 fails on non-zero leaks

- **WHEN** `qemu-img check --output=json` returns `{"errors": 0, "leaks": 5, "corruptions": 0}`
- **THEN** M2 verification fails with an error message naming the leak count
