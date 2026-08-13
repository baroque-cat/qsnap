## Why

The hysteresis collapse currently merges snapshots one at a time (`virsh blockcommit` per
snapshot, capped at `max_commits_per_run = 12` per run), so converging from the trigger
threshold (N=73) to the floor (L=24) takes ~5 hourly runs. On busy VMs a single layer can
take ~16 minutes to merge, because every per-layer job re-copies clusters that newer layers
overwrite again (redundant I/O), pays per-job setup over a 60+ node backing chain, and each
batch repeats near-identical chain scans and verifications. `virsh blockcommit` natively
supports merging a whole chain *segment* (`--base … --top …`) in one job, copying each cluster
at most once — the collapse can finish in a single run while keeping the same safety floor.

## What Changes

- **Single-shot collapse**: when the hysteresis trigger fires (`N > snapshot_chain_length`),
  Core merges ALL oldest `N − snapshot_preserve_min` snapshots down to the floor within the
  SAME run. The collapse moment and the remainder are computed per-disk from config exactly as
  today (H = `snapshot_chain_length`, L = `snapshot_preserve_min`); only the execution stops
  being portioned. One chunk, no batching across runs.
- **BREAKING** — `BlockCommitManager` (live virsh path) issues ONE
  `virsh blockcommit --domain <vm> --path <disk> --base <base> --top <newest removable>
  --delete --verbose --wait` per batch instead of one command per snapshot. Commit outcome
  becomes all-or-nothing for the live path; the newest `L` snapshots and the active layer
  remain untouched by construction.
- **BREAKING** — remove the `max_commits_per_run` global config option (no per-run cap
  remains; the whole remove set is committed in one job). Config files setting it SHALL fail
  validation with an actionable message pointing at the removal.
- **BREAKING** — remove the `collapse_in_progress` phase machinery: the `IStateManager`
  methods (`set/get/clear_collapse_in_progress`), the JSON state key, and the
  started/active/complete phase logging. A collapse either completes inside its run or is
  retried by the next run because `N > H` still holds; the commit intent journal
  (`commit_in_progress`) already covers crash recovery. Leftover keys in existing
  `{vm}.json` files SHALL be ignored (no migration required; unknown keys are already
  tolerated).
- **Timeout budget scaling**: `blockcommit_timeout` keeps its "per merged layer" semantics;
  the effective wall-clock budget passed to the single bulk job is
  `blockcommit_timeout × len(merge set)`. Timeout → `outcome="unknown"` → existing
  reconciliation/deferral machinery (unchanged).
- **Verification deduplication**: the pre-commit chain-length baseline SHALL be derived from
  the pre-commit `scan_backing_chain` result instead of a second full
  `qemu-img info --backing-chain` walk (same command, same data).
- **Offline path**: `QemuImgCommitManager` keeps its per-layer loop (qemu-img has no segment
  commit) but is no longer capped, so an offline collapse also converges in one run. Partial
  prefix reconciliation therefore remains necessary for the offline path only.
- **Observability**: intent and heartbeat log lines switch from per-snapshot naming to
  batch-level wording ("collapsing N snapshot(s) into base", "still collapsing N layer(s) into
  base (Xs elapsed)"). Dry-run predictions name the full oldest `N − L` set with no cap.

## Capabilities

### New Capabilities

None — this change replaces the existing collapse execution strategy rather than adding a
parallel mode (explicit owner decision: personal project, no backward compatibility needed).

### Modified Capabilities

- `hysteresis-retention`: collapse executes as a single bulk segment commit within one run;
  per-run cap and multi-run phase state removed; grow phase unchanged.
- `lifecycle-manager`: the live `BlockCommitManager` merges the whole merge set with ONE
  `virsh blockcommit` segment command (top = newest removable snapshot) instead of one
  command per snapshot; offline executor unchanged except uncapped.
- `core-orchestrator`: `_evaluate_disk_retention` hysteresis branch loses the phase marker
  and cap truncation steps; post-commit phase-convergence step removed; pre-commit chain
  length baseline derived from the pre-commit scan.
- `config-model`: `max_commits_per_run` option removed from `GlobalConfig`; rejection of the
  removed key documented.
- `state-management`: `collapse_in_progress` phase key and its `IStateManager` methods
  removed; leftover persisted keys tolerated.
- `dry-run-prediction`: collapse predictions name the full oldest `N − L` set (no cap, no
  phase-key reads).
- `commit-observability`: intent/heartbeat/merged log line wording updated for batch-level
  bulk jobs.
- `count-based-retention`: cross-reference text updated (threshold/floor only — no more
  phase/cap wording).

## Impact

- **Code**: `qsnap/core/__init__.py` (retention branch, `_blockcommit_one_disk`, dispatch,
  dry-run predictions, startup recovery), `qsnap/modules/lifecycle/blockcommit_manager.py`
  (single-segment command), `qsnap/models/config.py` + `qsnap/config/facade.py` (option
  removal + validation), `qsnap/interfaces/state.py` + `qsnap/state/json_manager.py` (phase
  key removal), `qsnap/cli/summary.py` only if wording surfaces there, `qsnap.toml.example`.
- **Interfaces**: `ILifecycleManager.blockcommit()` signature UNCHANGED (already receives the
  full merge-set list). `IStateManager` SHRINKS (three methods removed) — **BREAKING** for all
  implementations and mocks (`JsonStateManager`, `InMemoryStateManager`, test mocks).
- **Factory**: no new branches; `DefaultFactory.create_lifecycle_manager(mode)` unchanged.
- **State files**: `/var/lib/qsnap/state/{vm}.json` may still contain stale
  `collapse_in_progress` keys after upgrade — readers ignore them; nothing writes them.
- **External dependencies**: requires libvirt/QEMU behavior of segment `blockcommit --delete`
  on deep chains (standard since libvirt 6.x); no new binaries, no new Python deps.
- **Docs/config**: `qsnap.toml.example` loses the `max_commits_per_run` stanza and documents
  the single-shot collapse.
- **Tests**: substantial refactor — drip-mode unit/core tests asserting capped batches and
  phase transitions must be deleted or rewritten; new unit tests for the bulk manager and
  uncapped retention; integration/stress tests extended to verify real segment commits
  (chain length delta, file deletion, floor preservation) against a disposable libvirt VM.
