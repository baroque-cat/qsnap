## 1. F1–F4 — Mutation gates in Core

Specs: `specs/dry-run-prediction/spec.md`, `specs/core-orchestrator/spec.md`. Design: D1.

- [x] 1.1 `_preflight_cleanup`: three `rm -f` sites (stale `*.tmp`/`*.partial`, NBD sockets, truncated qcow2) → `[dry-run] Would remove stale file/socket/partial transfer` logs, no `rm`, `removed_count` untouched
- [x] 1.2 `_check_deferred_thresholds`: `update_deferred_warning()` gated by `if not self._dry_run`
- [x] 1.3 `_blockcommit_snapshots` stale-state self-healing: dry-run logs `[dry-run] Would remove stale state entry`, no `remove_snapshot()`
- [x] 1.4 `check --deep`: `_set_last_deep_check_time()` gated by `if deep and not self._dry_run`

## 2. F5 — CLI SUPPRESS pattern

Specs: `specs/cli-interface/spec.md`. Design: D2.

- [x] 2.1 `cli/app.py`: restore `--dry-run` → `default=argparse.SUPPRESS` + `-n` alias
- [x] 2.2 `cli/app.py`: local `--dry-run`/`-n` with SUPPRESS on `run`/`snapshot`/`backup`/`prune`
- [x] 2.3 Scenario `cli-interface` "--print-schedule with --dry-run" (`qsnap run --print-schedule --dry-run`) now parses — fixed by 2.2, scenario text unchanged

## 3. F6 — Docstring and spec wording for error records

Specs: `specs/action-audit-trail/spec.md`, `specs/core-orchestrator/spec.md`. Design: D3.

- [x] 3.1 `PipelineResult.actions` docstring: dry-run contains only error records
- [x] 3.2 Spec deltas align action-audit-trail and core-orchestrator dry-run wording

## 4. Tests

- [x] 4.1 argparse: `qsnap --dry-run restore SNAP` → `dry_run is True`; `-n` alias; both flag positions for run/snapshot/backup/prune
- [x] 4.2 Regression L1: no `rm` issued in dry-run with stale `*.tmp` present; files survive
- [x] 4.3 Regression L2: no `update_deferred_warning` call in dry-run
- [x] 4.4 Regression L3: no `remove_snapshot` call for stale entries in dry-run; log emitted
- [x] 4.5 Regression L4: `check --deep` dry-run does not create `_last_deep_check.json`
- [x] 4.6 Full non-integration suite + ruff green

## 5. Validation

- [x] 5.1 `openspec validate fix-dry-run-mutations --strict`
- [x] 5.2 Archive the change (syncs delta specs into main specs)
