# Proposal: hysteresis-snapshot-retention

## Why

The 2026-08-12 production incident exposed two related weaknesses. First, steady-state
count-based retention keeps every backing chain permanently at maximum depth
(`chain_length`..`chain_length+1`) and issues one `virsh blockcommit` every hour for every
VM — maximizing exposure to the fragile libvirt deep-commit path (the same path that hung a
client for 60 minutes in the incident) and contradicting the operator's intent of "keep the
last 24 hours of hourly snapshots, collapse the rest occasionally". Second, the backup-side
block-job probe passes the **base image path** to `virsh blockjob --path`; with external
snapshots the domain XML resolves only the active overlay, so libvirt answers
`invalid argument: disk '...img.qcow2' not found in domain` on every backup run — journal
noise plus a silently dead safety gate that can never detect an active block job.

## What Changes

- New global/per-VM option `snapshot_retention_mode = "steady" | "hysteresis"`
  (default `"hysteresis"` — this change makes hysteresis the out-of-the-box retention mode).
- In `"hysteresis"` mode the existing knobs are reinterpreted: `snapshot_chain_length`
  becomes the **trigger threshold H** (no commits while `N ≤ H`), `snapshot_preserve_min`
  becomes the **collapse floor L** (when triggered, the oldest `N − L` snapshots are merged
  into the base until `N ≤ L`). Validation enforces `H > L ≥ 1`.
- Collapse is a persisted per-disk **phase**: once triggered it continues across subsequent
  runs until `N ≤ L`, then the chain grows again. New additive state key
  `collapse_in_progress` (missing key = inactive; no migration needed).
- New global option `max_commits_per_run` (default `12`, `0` = unlimited): caps how many
  snapshots one run may merge, bounding run duration and making first-time migration from a
  deep steady-state chain gradual and safe.
- Backup-side block-job probe fix: address the disk by its **target name** (`vda`) instead
  of the base image path; classify the result `none | active | error`; `active` defers the
  backup for this run; `error` logs a WARNING and proceeds (fail-open, documented).
- Dry-run predicts hysteresis outcomes exactly: silent growth below the threshold, full
  batch collapse predictions above it, including the per-run cap.
- Rollback-safe: old code ignores unknown state keys and the new config options are inert
  unless the mode flag is set.

## Capabilities

### New Capabilities

- `hysteresis-retention`: Trigger-threshold / collapse-floor retention policy for the
  snapshot world, the persisted collapse phase, and the per-run commit cap.

### Modified Capabilities

- `count-based-retention`: `chain_length` gains a second semantic (trigger threshold)
  gated by the retention mode; hysteresis mode becomes the default, steady mode remains
  available and is unchanged.
- `snapshot-preserve-min`: `preserve_min` additionally serves as the post-collapse floor in
  hysteresis mode; its steady-mode trimming semantics are unchanged.
- `config-model`: new options `snapshot_retention_mode` (global, VM-inheritable) and
  `max_commits_per_run` (global), with validation rules.
- `core-orchestrator`: retention evaluation branches on the mode; collapse-phase lifecycle
  (set on trigger, clear on reaching the floor); commit-set truncation by
  `max_commits_per_run`.
- `state-management`: new per-VM state key `collapse_in_progress` (per-disk phase markers),
  atomic read/write, cleared by reset operations.
- `blockjob-protocol`: all block-job probes SHALL address disks by target device name;
  probing by base-image path is prohibited for domains with external snapshots.
- `backup-provider`: the pre-backup block-job probe uses the target name and the
  none/active/error classification with defined defer/proceed semantics.
- `commit-reconciliation`: reconciliation learns to converge a partially completed
  multi-snapshot merge set (largest vanished oldest prefix) instead of deferring it
  forever — required once merge sets can exceed one snapshot.
- `dry-run-prediction`: predictions cover hysteresis growth silence, batch collapses, and
  the per-run cap with zero mutations.

## Impact

- **Code:** `qsnap/core/__init__.py` (retention branch, phase lifecycle, cap),
  `qsnap/models/config.py` + `qsnap/config/facade.py` (options + validation),
  `qsnap/state/json_manager.py` + `qsnap/interfaces/state.py` + mocks (phase markers),
  `qsnap/modules/backup/bitmap.py` (probe fix), `qsnap.toml.example`, README.
- **No ABC breakage:** all state additions are additive methods; module contracts
  (config-as-parameter, result objects, no cross-module imports) are preserved.
- **Operational:** hysteresis is the default retention mode (band H=72, L=24); hosts with
  existing deep chains converge gradually via the capped catch-up (≤ `max_commits_per_run`
  merges per hourly run). Setting `snapshot_retention_mode = "steady"` restores the
  pre-existing count-based behavior.
- **Tests:** unit (retention matrix, config validation, state round-trip), integration
  (multi-run collapse simulation, probe semantics against real libvirt), stress (long-chain
  collapse), dry-run parity. Some obsolete steady-only assertions will be refactored.
