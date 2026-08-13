## REMOVED Requirements

### Requirement: collapse_in_progress phase key
**Reason**: The phase key tracked a collapse that was spread across multiple runs by the per-run commit cap. The collapse is now a single uncapped bulk blockcommit completed within one run (capability `hysteresis-retention`), so there is no cross-run phase to persist. Crash recovery during a long bulk job is fully covered by the commit intent journal (`commit_in_progress`, capability `commit-intent-journal`), which already stores the full merge set, base image, and timestamp before the irreversible command.

**Migration**: BREAKING interface shrinkage — remove `set_collapse_in_progress`, `clear_collapse_in_progress`, and the collapse-phase reader from `IStateManager`, `JsonStateManager`, and every mock (`InMemoryStateManager`, test mocks). Persisted `collapse_in_progress` keys left inside existing `/var/lib/qsnap/state/{vm}.json` files require NO migration: JSON readers already tolerate unknown keys, and nothing reads or writes the key anymore. `reset_vm_state` / `reset_vm_disk_state` stop touching the key (a stale key in a reset file is harmless and may remain).
