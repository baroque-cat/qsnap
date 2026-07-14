## Context

qsnap currently copies snapshot files to backup targets with plain `cp`, offering no bandwidth control. On production hosts with high I/O requirements, a large snapshot transfer can saturate the disk subsystem and impact running VMs. Additionally, deferred blockcommit operations (queued when AppArmor/SELinux denies `virsh blockcommit`) accumulate silently — users only discover them by manually inspecting state files or logs.

The existing architecture already supports option inheritance (global → VM → target) for retention fields. Rate limiting should follow the same pattern. The deferred-operations mechanism (via `IStateManager` / `DeferredBlockcommit`) is already in place for persistence — we extend it with monitoring, not replace it.

## Goals / Non-Goals

**Goals:**

1. Provide opt-in bandwidth control for backup transfers with per-target granularity
2. Replace `cp` with `rsync` as the primary transfer tool, gaining partial-transfer resilience as a side benefit
3. Give users visibility into accumulated deferred blockcommits via CLI (`list deferred`)
4. Proactively alert on excessive deferred accumulation via configurable thresholds
5. Integrate deferred status into `qsnap check` as a unified health check point

**Non-Goals:**

- Ratelimiting `qemu-img convert` (full backup) or NBD bitmap transfers — only file-copy transfer
- Notification delivery (email, webhook) — this lays groundwork (`last_warned_at` field) but actual notification implementation is a separate feature
- Automatic AppArmor/SELinux policy creation — we provide diagnostic guidance only
- Parallel backup transfers or bandwidth sharing across VMs

## Decisions

### D1: Replace `cp` with `rsync --bwlimit` for file-copy transfers

**Rationale:** `rsync --bwlimit` provides bandwidth control, `--partial` for resume-after-interruption, `--progress` for observable throughput, and preserves file permissions (no separate `chmod` needed). It is universally available on Linux systems.

**Alternatives considered:**

| Alternative | Why rejected |
|---|---|
| `pv -L <rate>` pipe | Does not preserve permissions; no `--partial` resilience; not pre-installed on many distros |
| `cp` + `ionice -c 3` | No bandwidth control — only I/O scheduler priority, unpredictable |
| `curl --limit-rate` | Only for URLs, not local filesystem |

**Fallback:** When `rate_limit` is set but `rsync` is not found, log a WARNING and fall back to `cp` (same behavior as before this change). This is a non-blocking degradation — backups continue without rate limiting.

### D2: Rate limit config uses global default + per-target override (option inheritance)

**Rationale:** Mirrors the existing inheritance pattern for `target_preserve` and `target_preserve_min`. A global `rate_limit = "100M"` applies to all targets; individual targets can override with their own value. Follows the principle of least surprise for existing users.

**Config shape:**

```toml
rate_limit = "100M"            # global default (in GlobalConfig)

[[vm]]
  [[vm.target]]
  path = "/mnt/backup-fast"
  rate_limit = "no"            # override: unlimited

  [[vm.target]]
  path = "/mnt/backup-slow"
  # inherits global "100M"
```

**Parsing rules:**
- `"no"` or `"0"` → no limit (default)
- `"<number><suffix>"` → parse suffix: K=KiB, M=MiB, G=GiB, T=TiB (binary powers of 1024)
- Invalid format → config parse error at startup (fail-fast)
- `rsync` accepts KiB/s: `rate_limit_bytes // 1024`

### D3: Rate limit field uses binary-suffix format (`"100M"`)

**Rationale:** Matches `rsync --bwlimit` expectations and common I/O tool conventions (`dd`, `pv`, `rsync`). Binary-based (1024) rather than decimal (1000) because disk/storage measurements are binary. Human-readable and unambiguous.

### D4: Deferred thresholds are per-VM, checked at end of pipeline run

**Rationale:** Deferred operations are tracked per-VM in `IStateManager`. Each VM's backlog is independent — a VM with 15 deferred ops is a problem even if another VM has 0. The check runs after the pipeline so all newly-deferred operations are included. Threshold violations produce log messages (WARNING/CRITICAL) but do NOT change the exit code — they are diagnostic, not pipeline failures.

**Threshold config:**

```toml
deferred_warn_count = 5      # WARNING if >= 5 deferred for a VM
deferred_crit_count = 10     # CRITICAL if >= 10
deferred_warn_age = "7d"     # WARNING if oldest > 7 days
deferred_crit_age = "14d"    # CRITICAL if oldest > 14 days
```

**Why both count AND age?** 3 operations over 2 days is normal (VM will likely shut down soon). 3 operations over 30 days means the VM never shuts down — the problem won't self-resolve, and intervention is needed.

### D5: `DeferredBlockcommit` gains optional `last_warned_at` field

**Rationale:** Enables future notification deduplication without requiring the notification system to exist yet. Default `None` — backward compatible with existing state files (JSON deserialization handles missing keys gracefully). When the notification feature is implemented, it can:
- Send WARNING alerts at most once per day (check `last_warned_at`)
- Send CRITICAL alerts on every run
- Send "resolved" notification when count drops to 0
- Record `last_warned_at = datetime.now()` after each alert

### D6: `qsnap check` integrates deferred status with remediation guidance

**Rationale:** `check` is the single health-check entry point. Users running `qsnap check` expect a complete picture. Adding deferred status here makes the feature discoverable. Including remediation guidance (specific commands for AppArmor/SELinux) turns `check` from a passive diagnostic into an active troubleshooting tool.

**Example output:**

```
VM mail-server:      WARNING
  Backing chain:     OK
  Deferred:          CRITICAL — 12 operations pending (oldest: 14d 3h, reason: apparmor)
  → Merge blocked by AppArmor. Consider: aa-disable /etc/apparmor.d/libvirt/libvirt-<uuid>
  → Or: shut down the VM to allow automatic merge.
```

## Risks / Trade-offs

- **[Risk] Rsync adds a new required dependency** → Mitigation: graceful fallback to `cp` with WARNING log when rsync is absent. Rate limiting is an opt-in feature.
- **[Risk] Users upgrading from old state files will have missing `last_warned_at` in DeferredBlockcommit** → Mitigation: `last_warned_at` defaults to `None`; `_dict_to_deferred()` in JsonStateManager uses `.get("last_warned_at")` which returns `None` for missing keys.
- **[Risk] `rsync --partial` leaves incomplete files on target disk** → Mitigation: already handled — `transfer_missing()` checks file existence and size. With `--partial`, rsync will resume an interrupted transfer on the next run rather than producing a corrupt file.
- **[Trade-off] Rate limit only on file-copy, not on `qemu-img convert` or NBD** → Acceptable: full backups (`qemu-img convert`) are rare (controlled by `full_every`) and already resource-heavy; NBD bitmap backups transfer only dirty blocks and are inherently smaller. The main frequent I/O load is the `cp` operation in `transfer_missing`.
- **[Trade-off] Exit code unchanged for threshold violations** → Deferred threshold breaches are operational alerts, not pipeline failures. The backup pipeline succeeded — the inability to merge is a separate concern. Users who want CI/CD integration can parse log output.

## Open Questions

None — all design decisions are settled per the planning discussion (`plan.md`).
