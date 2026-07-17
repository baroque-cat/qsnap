## MODIFIED Requirements

### Requirement: Restore command copies backup chain to target directory
The `qsnap restore` command SHALL identify a backup by its snapshot name, copy the backup file and its entire backing chain to a specified target directory, and rebuild backing paths using `qemu-img rebase -u` with relative `./` prefixes. The chain SHALL be resolved starting from the backup file, following backing file references through FULL anchors and incremental layers until a standalone file (no backing) is reached.

The chain file path extraction from `qemu-img info --backing-chain --output=json` SHALL accept both `"image"` (legacy QEMU) and `"filename"` (QEMU 11.0+) as the key for the disk image file path in each chain entry.

#### Scenario: Restore a file-copy backup chain with FULL anchor
- **WHEN** `qsnap restore debiantest.20250101T1200 /restore/path` is executed
- **AND** the backup chain includes a FULL anchor `vm.FULL.20250101.qcow2`
- **THEN** the FULL anchor and all incremental files in the chain are copied to `/restore/path/`
- **THEN** `qemu-img rebase -u -b ./vm.FULL.20250101.qcow2` is run on each incremental
- **THEN** the command outputs the path to the active (top) image in the restored chain

#### Scenario: Restore chain with new QEMU format
- **WHEN** `qsnap restore debiantest.20250101T1200 /restore/path` is executed
- **AND** `qemu-img info` output uses `"filename"` keys (QEMU 11.0+ format)
- **THEN** the chain is correctly parsed and all files are copied to `/restore/path/`

#### Scenario: Restore a nonexistent backup
- **WHEN** `qsnap restore nonexistent-snap /restore/path` is executed
- **THEN** the command exits with code 1 and an error message

#### Scenario: Target directory does not exist
- **WHEN** `qsnap restore snap.20250101 /nonexistent/path` is executed
- **THEN** the command exits with code 1 and an error message indicating the directory must exist
