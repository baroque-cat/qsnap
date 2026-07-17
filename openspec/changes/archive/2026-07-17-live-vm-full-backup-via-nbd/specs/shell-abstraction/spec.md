## ADDED Requirements

### Requirement: --force-share safety classification for qemu-img operations

The system SHALL classify `qemu-img` operations into two categories for `--force-share` usage:

**SAFE (metadata-only):** `qemu-img info`, `qemu-img info --backing-chain`, `qemu-img map`, `qemu-img check`, `qemu-img rebase -u`. These operations read only headers, L2 tables, and refcount structures — minimal I/O with low risk of reading inconsistent data. `--force-share` SHALL be used on these operations when the target file may be the active layer of a running VM.

**DANGEROUS (data-copying):** `qemu-img convert`, `qemu-img compare`, `qemu-img commit`. These operations read ALL data clusters — race conditions during concurrent QEMU writes produce silently corrupted output (missed writes, stale data, partial writes). `--force-share` SHALL NOT be used on these operations. Instead, the NBD pull-model SHALL be used for live-VM data-copying operations (FULL backup, fork). For offline operations (lifecycle commit), the VM MUST be stopped — `--force-share` would mask a dangerous state.

#### Scenario: Metadata-only operation uses --force-share on active layer
- **WHEN** `qemu-img info` is called on a file that is the active layer of a running VM
- **THEN** `--force-share` is included in the command
- **AND** the command succeeds despite the VM holding a write lock

#### Scenario: Data-copying operation does NOT use --force-share
- **WHEN** `qemu-img convert` or `qemu-img compare` is called on a file that is the active layer
- **THEN** `--force-share` is NOT included in the command
- **AND** the NBD pull-model is used instead (for FULL backup and fork)
- **AND** if the VM is stopped, direct operation is safe (no lock holder)

#### Scenario: Lifecycle commit operations remain offline-only
- **WHEN** `qemu-img commit` is called by `BlockCommitManager` or `QemuImgCommitManager`
- **THEN** `--force-share` is NOT added
- **AND** the operation is only safe when the VM is stopped (intentionally offline)
- **AND** if the VM is running, the operation may fail with a lock error (correct behavior — prevents dangerous commit on live disk)
