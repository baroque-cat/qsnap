## MODIFIED Requirements

### Requirement: Hash verification tier (verify="hash")

`verify_backup` SHALL support `verify_mode="hash"` — see `specs/backup-hash-verification/spec.md` for the authoritative spec. Hash verification is the recommended default for file-copy (rsync) mode (race-condition-immune, hash computed at snapshot creation time). Hash verification is NOT supported in bitmap (NBD) mode because NBD-converted qcow2 files have different internal structure. When bitmap mode is configured with `verify="hash"`, ConfigFacade SHALL log a WARNING and auto-downgrade to `"metadata"`.

Full verification (`verify="full"`) is also NOT supported in bitmap (NBD) mode for incremental transfers. An incremental NBD export produces a standalone qcow2 containing only dirty blocks (non-dirty blocks read as zeros), while the source snapshot (with backing chain) resolves to full data. `qemu-img compare` between these will always mismatch. When bitmap mode is configured with `verify="full"`, ConfigFacade SHALL log a WARNING and auto-downgrade to `"metadata"`.

#### Scenario: Bitmap mode with verify=hash auto-downgrades
- **WHEN** `incremental_mode == "bitmap"` and `verify == "hash"` is explicitly configured
- **THEN** ConfigFacade logs a WARNING: "verify='hash' is not supported in bitmap mode (NBD-converted qcow2 has different internal structure). Downgrading to verify='metadata'. Use verify='full' for content-level verification."
- **AND** the effective `verify` value is `"metadata"`

#### Scenario: Bitmap mode with verify=full auto-downgrades
- **WHEN** `incremental_mode == "bitmap"` and `verify == "full"` is explicitly configured
- **THEN** ConfigFacade logs a WARNING: "verify='full' is not supported in bitmap mode (incremental NBD exports contain only dirty blocks; qemu-img compare will always mismatch against source with backing chain). Downgrading to verify='metadata'."
- **AND** the effective `verify` value is `"metadata"`

#### Scenario: Bitmap mode with verify=metadata (default) works correctly
- **WHEN** `incremental_mode == "bitmap"` and `verify` is unset or set to `"metadata"`
- **THEN** no WARNING is logged
- **AND` the effective `verify` value is `"metadata"`

#### Scenario: File-copy mode retains verify=full
- **WHEN** `incremental_mode == "file-copy"` and `verify == "full"` is explicitly configured
- **THEN** no downgrade occurs
- **AND** `qemu-img compare` is used for post-transfer verification (rsync produces byte-identical copies)

#### Scenario: File-copy mode retains verify=hash
- **WHEN** `incremental_mode == "file-copy"` and `verify == "hash"` is explicitly configured
- **THEN** no downgrade occurs
- **AND** SHA-256 hash verification is used (rsync produces byte-identical copies)
