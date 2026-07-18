## MODIFIED Requirements

### Requirement: M1 metadata verification of FULL before rebase

When `FileCopyBackupProvider.transfer_missing()` rebases an incremental to a FULL anchor, it SHALL call M1 verification on the FULL anchor using the verification mode from `GlobalConfig.full_verify_before_rebase`. If the configured mode is `"off"`, verification SHALL be skipped. If M1 fails, an alternative (older) FULL anchor SHALL be tried. If no valid anchor exists, rebase is skipped with a WARNING. The SHALL NOT hardcode `"metadata"` — the verification mode SHALL be passed as a parameter from the caller.

#### Scenario: Rebase with full_verify_before_rebase = "metadata"
- **WHEN** `GlobalConfig.full_verify_before_rebase` is `"metadata"`
- **AND** `FileCopyBackupProvider.transfer_missing()` rebases to a FULL anchor
- **THEN** M1 verification (qemu-img info format + corrupt-bit check) is performed on the anchor

#### Scenario: Rebase with full_verify_before_rebase = "off"
- **WHEN** `GlobalConfig.full_verify_before_rebase` is `"off"`
- **AND** `FileCopyBackupProvider.transfer_missing()` rebases to a FULL anchor
- **THEN** no verification is performed on the anchor

#### Scenario: Rebase with full_verify_before_rebase = "check"
- **WHEN** `GlobalConfig.full_verify_before_rebase` is `"check"`
- **AND** `FileCopyBackupProvider.transfer_missing()` rebases to a FULL anchor
- **THEN** M1 + M2 (qemu-img check) verification is performed on the anchor

#### Scenario: Verification mode passed as parameter
- **WHEN** `Core._backup_target()` calls `provider.transfer_missing(...)`
- **THEN** the call includes the verification mode read from `self._config.get_global().full_verify_before_rebase`
- **AND** the provider does NOT hardcode a verification mode
