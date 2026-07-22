"""ConfigFacade — TOML config parser with option inheritance.

Parses TOML configuration, resolves option inheritance (global → VM →
target), and produces immutable frozen dataclasses.  Implements
``IConfigFacade``.
"""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path
from typing import cast

from qsnap.interfaces.config import IConfigFacade
from qsnap.models.config import GlobalConfig, TargetConfig, VMConfig
from qsnap.retention.time_based import parse_stall_timeout

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when the configuration is invalid (malformed TOML, missing fields)."""


def _is_valid_duration(raw: str) -> bool:
    """Check whether *raw* is a valid duration string like ``"2s"`` or ``"10s"``."""
    return bool(re.match(r"^\d+s$", raw))


class ConfigFacade(IConfigFacade):
    """Concrete config facade that parses a TOML file.

    Global options defined at the top level apply as defaults to all
    VMs.  VM-level options override globals.  Target-level options
    override both VM and global.
    """

    def __init__(self, config_path: str | Path) -> None:
        self._config_path = Path(config_path)
        self._global: GlobalConfig
        self._vms: list[VMConfig]
        self._vm_map: dict[str, VMConfig]
        self._parse()

    @property
    def config_path(self) -> Path:
        return self._config_path

    # ── internal helpers ───────────────────────────────────────────────

    def _parse(self) -> None:
        try:
            with open(self._config_path, "rb") as fh:
                raw = tomllib.load(fh)
        except FileNotFoundError as exc:
            raise ConfigError(f"Config file not found: {self._config_path}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Invalid TOML in {self._config_path}: {exc}") from exc

        # Build global config from top-level keys.
        global_kwargs: dict[str, str | int | bool | None] = {}
        for key in (
            "timestamp_format",
            "preserve_day_of_week",
            "state_dir",
            "lockfile",
            "snapshot_preserve",
            "target_preserve",
            "snapshot_preserve_min",
            "target_preserve_min",
            "deferred_warn_count",
            "deferred_crit_count",
            "deferred_warn_age",
            "deferred_crit_age",
        ):
            if key in raw:
                global_kwargs[key] = str(raw[key])

        # Parse fault-tolerance safety fields (T0/T1 fast ON by default,
        # T3 heavy OFF by default).
        if "auto_cleanup" in raw:
            global_kwargs["auto_cleanup"] = bool(raw["auto_cleanup"])
        if "state_backup_count" in raw:
            global_kwargs["state_backup_count"] = int(raw["state_backup_count"])
        if "chain_verify_before_commit" in raw:
            global_kwargs["chain_verify_before_commit"] = bool(raw["chain_verify_before_commit"])
        if "chain_verify_after_commit" in raw:
            global_kwargs["chain_verify_after_commit"] = bool(raw["chain_verify_after_commit"])
        if "deep_check_schedule" in raw:
            global_kwargs["deep_check_schedule"] = str(raw["deep_check_schedule"])

        # Compress full backups (global default).
        if "compress" in raw:
            global_kwargs["compress"] = bool(raw["compress"])

        # Compression algorithm (zstd default, zlib alternative).
        if "compression_type" in raw:
            global_kwargs["compression_type"] = str(raw["compression_type"])

        # Stall detection timeout for data-transfer commands.
        if "backup_stall_timeout" in raw:
            global_kwargs["backup_stall_timeout"] = str(raw["backup_stall_timeout"])

        # FULL backup integrity verification tiers (M1/M2/M3).
        if "full_verify_after_create" in raw:
            global_kwargs["full_verify_after_create"] = str(raw["full_verify_after_create"])
        if "full_verify_before_rebase" in raw:
            global_kwargs["full_verify_before_rebase"] = str(raw["full_verify_before_rebase"])
        if "full_verify_before_delete" in raw:
            global_kwargs["full_verify_before_delete"] = str(raw["full_verify_before_delete"])
        if "deep_check_targets" in raw:
            global_kwargs["deep_check_targets"] = bool(raw["deep_check_targets"])

        # Transaction log (optional absolute path).
        if "transaction_log" in raw:
            global_kwargs["transaction_log"] = str(raw["transaction_log"])

        self._global = GlobalConfig(**global_kwargs)  # type: ignore[arg-type]

        # rate_limit is deprecated (removed backup strategy) — log a
        # warning naming the field and ignore the value (design D3).
        if "rate_limit" in raw:
            logging.getLogger("qsnap.config").warning(
                "rate_limit is deprecated and ignored — NBD bitmap backups "
                "do not support transfer throttling. "
                "Remove rate_limit from your config."
            )

        # Validate preserve_day_of_week.
        valid_days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
        if self._global.preserve_day_of_week.lower() not in valid_days:
            raise ConfigError(
                f"Invalid preserve_day_of_week: {self._global.preserve_day_of_week!r}. "
                f"Must be one of: {', '.join(sorted(valid_days))}"
            )

        # Validate deep_check_schedule.
        valid_schedules = {"off", "weekly", "monthly"}
        if self._global.deep_check_schedule.lower() not in valid_schedules:
            raise ConfigError(
                f"Invalid deep_check_schedule: {self._global.deep_check_schedule!r}. "
                f"Must be one of: {', '.join(sorted(valid_schedules))}"
            )

        # Validate compression_type (zstd default, zlib alternative).
        valid_compression = {"zstd", "zlib"}
        if self._global.compression_type.lower() not in valid_compression:
            raise ConfigError(
                f"Invalid compression_type: {self._global.compression_type!r}. "
                f"Must be one of: {', '.join(sorted(valid_compression))}"
            )

        # Validate backup_stall_timeout via parse_stall_timeout().
        try:
            parse_stall_timeout(self._global.backup_stall_timeout)
        except ValueError as exc:
            raise ConfigError(
                f"Invalid backup_stall_timeout: {self._global.backup_stall_timeout!r}. "
                f"Must be a duration string like '30m', '1h', '0s'."
            ) from exc

        # Validate FULL verification tiers.
        valid_after_create = {"metadata", "check", "hash", "off"}
        if self._global.full_verify_after_create.lower() not in valid_after_create:
            raise ConfigError(
                f"Invalid full_verify_after_create: "
                f"{self._global.full_verify_after_create!r}. "
                f"Must be one of: {', '.join(sorted(valid_after_create))}"
            )
        valid_before_rebase = {"metadata", "off"}
        if self._global.full_verify_before_rebase.lower() not in valid_before_rebase:
            raise ConfigError(
                f"Invalid full_verify_before_rebase: "
                f"{self._global.full_verify_before_rebase!r}. "
                f"Must be one of: {', '.join(sorted(valid_before_rebase))}"
            )
        valid_before_delete = {"metadata", "check", "off"}
        if self._global.full_verify_before_delete.lower() not in valid_before_delete:
            raise ConfigError(
                f"Invalid full_verify_before_delete: "
                f"{self._global.full_verify_before_delete!r}. "
                f"Must be one of: {', '.join(sorted(valid_before_delete))}"
            )

        # Build VM configs.
        vm_sections = cast(list[dict[str, object]], raw.get("vm", []))
        if not isinstance(vm_sections, list):  # type: ignore[reportUnnecessaryIsInstance]
            raise ConfigError("[[vm]] must be an array of tables")

        self._vms = []
        self._vm_map = {}
        for vm_raw in vm_sections:
            vm_config = self._build_vm(vm_raw, self._global)
            self._vms.append(vm_config)
            self._vm_map[vm_config.name] = vm_config

    @staticmethod
    def _build_vm(
        vm_raw: dict[str, object],
        global_cfg: GlobalConfig,
    ) -> VMConfig:
        # Validate required fields.
        for field_name in ("name", "base_image", "snapshot_dir"):
            if field_name not in vm_raw:
                raise ConfigError(f"Missing required VM field: {field_name!r}")

        name = str(vm_raw["name"])
        base_image = Path(str(vm_raw["base_image"]))
        snapshot_dir = Path(str(vm_raw["snapshot_dir"]))

        # snapshot_create: VM-level or default "always".
        snapshot_create = str(vm_raw.get("snapshot_create", "always"))

        # snapshot_quiesce: VM-level or default False.
        snapshot_quiesce = bool(vm_raw.get("snapshot_quiesce", False))

        # lifecycle_mode: "virsh" (default) or "qemu-img".
        lifecycle_mode = str(vm_raw.get("lifecycle_mode", "virsh"))

        # change_detection_mode: "allocation-size" (default) or "allocation-map".
        change_detection_mode = str(vm_raw.get("change_detection_mode", "allocation-size"))

        # Deep verification fields (T2 — per-VM, default OFF).
        blockcommit_deep_verify = bool(vm_raw.get("blockcommit_deep_verify", False))
        snapshot_deep_verify = bool(vm_raw.get("snapshot_deep_verify", False))

        # snapshot_preserve: VM overrides global.
        snapshot_preserve: str | None
        if "snapshot_preserve" in vm_raw:
            snapshot_preserve = str(vm_raw["snapshot_preserve"])
        else:
            snapshot_preserve = global_cfg.snapshot_preserve

        # target_preserve: VM overrides global (target may override VM).
        target_preserve: str | None
        if "target_preserve" in vm_raw:
            target_preserve = str(vm_raw["target_preserve"])
        else:
            target_preserve = global_cfg.target_preserve

        # snapshot_preserve_min: VM overrides global.
        snapshot_preserve_min: str | None
        if "snapshot_preserve_min" in vm_raw:
            snapshot_preserve_min = str(vm_raw["snapshot_preserve_min"])
        else:
            snapshot_preserve_min = global_cfg.snapshot_preserve_min

        # target_preserve_min: VM overrides global (target may override VM).
        target_preserve_min: str | None
        if "target_preserve_min" in vm_raw:
            target_preserve_min = str(vm_raw["target_preserve_min"])
        else:
            target_preserve_min = global_cfg.target_preserve_min

        # disks: optional explicit list of disk targets.
        disks_raw = vm_raw.get("disks")
        if disks_raw is not None:
            if not isinstance(disks_raw, list):
                raise ConfigError("'disks' must be an array of strings")
            disks: list[str] | None = [str(d) for d in disks_raw]  # type: ignore[unknown-argument-type, unknown-variable-type]
        else:
            disks = None

        # Build targets.
        target_sections = cast(list[dict[str, object]], vm_raw.get("target", []))
        if not isinstance(target_sections, list):  # type: ignore[reportUnnecessaryIsInstance]
            raise ConfigError("[[vm.target]] must be an array of tables")

        targets: list[TargetConfig] = []
        for tgt_raw in target_sections:
            targets.append(
                ConfigFacade._build_target(
                    tgt_raw,
                    target_preserve,
                    target_preserve_min,
                    global_cfg.compress,
                    global_cfg.compression_type,
                    global_cfg.backup_stall_timeout,
                )
            )

        return VMConfig(
            name=name,
            base_image=base_image,
            snapshot_dir=snapshot_dir,
            snapshot_create=snapshot_create,
            snapshot_preserve=snapshot_preserve,
            target_preserve=target_preserve,
            snapshot_preserve_min=snapshot_preserve_min,
            target_preserve_min=target_preserve_min,
            snapshot_quiesce=snapshot_quiesce,
            lifecycle_mode=lifecycle_mode,
            change_detection_mode=change_detection_mode,
            disks=disks,
            blockcommit_deep_verify=blockcommit_deep_verify,
            snapshot_deep_verify=snapshot_deep_verify,
            targets=targets,
        )

    @staticmethod
    def _build_target(
        tgt_raw: dict[str, object],
        vm_target_preserve: str | None,
        vm_target_preserve_min: str | None = None,
        global_compress: bool = True,
        global_compression_type: str = "zstd",
        global_backup_stall_timeout: str = "30m",
    ) -> TargetConfig:
        if "path" not in tgt_raw:
            raise ConfigError("Missing required target field: 'path'")

        path = Path(str(tgt_raw["path"]))
        incremental = bool(tgt_raw.get("incremental", True))

        # Removed fields (removed backup strategy) — log a deprecation
        # WARNING naming the field and ignore the value (design D3,
        # same mechanism as the full_every deprecation below).
        if "incremental_mode" in tgt_raw:
            logging.getLogger("qsnap.config").warning(
                "incremental_mode is deprecated and ignored — NBD bitmap "
                "backup is now the only backup strategy. "
                "Remove incremental_mode from your config."
            )
        if "rate_limit" in tgt_raw:
            logging.getLogger("qsnap.config").warning(
                "rate_limit is deprecated and ignored — NBD bitmap backups "
                "do not support transfer throttling. "
                "Remove rate_limit from your config."
            )
        if "copy_base" in tgt_raw:
            logging.getLogger("qsnap.config").warning(
                "copy_base is deprecated and ignored — the first backup is "
                "always a FULL via qemu-img convert. "
                "Remove copy_base from your config."
            )

        # target_preserve: target overrides VM.
        target_preserve: str | None
        if "target_preserve" in tgt_raw:
            target_preserve = str(tgt_raw["target_preserve"])
        else:
            target_preserve = vm_target_preserve

        # target_preserve_min: target overrides VM.
        target_preserve_min: str | None
        if "target_preserve_min" in tgt_raw:
            target_preserve_min = str(tgt_raw["target_preserve_min"])
        else:
            target_preserve_min = vm_target_preserve_min

        # Validate: if target_preserve is set (not None, not "latest") and
        # all bucket counts are 0 and target_preserve_min is not "all",
        # the retention policy would keep nothing — raise ConfigError.
        if (
            target_preserve is not None
            and target_preserve != "latest"
            and target_preserve_min is not None
            and target_preserve_min != "all"
        ):
            # Parse bucket counts and F-anchors from the preserve string.
            # Regex: count, optional F prefix, bucket char.
            tokens = list(re.finditer(r"(\d+)(F?)([hdwmy])", target_preserve))
            counts = [int(m.group(1)) for m in tokens]
            f_anchors = [m for m in tokens if m.group(2) == "F"]

            # Validate: F-anchor on a bucket with count == 0 is rejected.
            for m in tokens:
                if m.group(2) == "F" and int(m.group(1)) == 0:
                    bucket = m.group(3)
                    raise ConfigError(f"F-anchor on bucket '{bucket}' requires count > 0")

            # Only reject if ALL bucket counts are 0 AND no F-anchors present.
            if counts and all(c == 0 for c in counts) and not f_anchors:
                raise ConfigError(
                    f"target_preserve={target_preserve!r} has all zero bucket "
                    f"counts and target_preserve_min={target_preserve_min!r} "
                    "is not 'all' — nothing would be retained. "
                    "Set at least one non-zero bucket count or use "
                    "target_preserve_min='all'."
                )

        # full_every is deprecated — log warning and ignore.
        if "full_every" in tgt_raw:
            logging.getLogger("qsnap.config").warning(
                "full_every is deprecated and ignored — FULL backups are now "
                "triggered by the retention policy's highest active bucket. "
                "Remove full_every from your config."
            )

        # compress: target overrides global default.
        # If full_compress (deprecated) is present and compress is not,
        # map full_compress → compress with a warning.
        if "compress" in tgt_raw:
            compress = bool(tgt_raw["compress"])
        elif "full_compress" in tgt_raw:
            logging.getLogger("qsnap.config").warning(
                "full_compress is deprecated — use 'compress' instead. "
                "Mapping full_compress → compress for this target."
            )
            compress = bool(tgt_raw["full_compress"])
        else:
            compress = global_compress

        # compression_type: target overrides global default.
        compression_type = str(tgt_raw.get("compression_type", global_compression_type))
        valid_compression = {"zstd", "zlib"}
        if compression_type.lower() not in valid_compression:
            raise ConfigError(
                f"Invalid compression_type: {compression_type!r}. "
                f"Must be one of: {', '.join(sorted(valid_compression))}"
            )

        # backup_stall_timeout: target overrides global default.
        backup_stall_timeout = str(tgt_raw.get("backup_stall_timeout", global_backup_stall_timeout))
        try:
            parse_stall_timeout(backup_stall_timeout)
        except ValueError as exc:
            raise ConfigError(
                f"Invalid backup_stall_timeout: {backup_stall_timeout!r}. "
                f"Must be a duration string like '30m', '1h', '0s'."
            ) from exc

        # verify: default "metadata", or explicit user value.  No
        # mode-dependence — bitmap is the only backup strategy; the
        # "hash"/"full" tiers run chain-traversing qemu-img compare via
        # verify_bitmap_incremental (live-source reliability caveat).
        verify_raw = tgt_raw.get("verify")
        if verify_raw is None:
            verify = "metadata"
        else:
            verify = str(verify_raw)
            # Validate user-provided value.
            if verify not in ("off", "metadata", "hash", "full"):
                raise ConfigError(
                    f"Invalid verify={verify!r}. Must be one of: off, metadata, hash, full."
                )

        # Backup retry fields (target-level — network reliability varies).
        backup_retry_max = cast(int, tgt_raw.get("backup_retry_max", 3))
        backup_retry_base = str(tgt_raw.get("backup_retry_base", "2s"))

        # Validate backup_retry_base — must be a duration string like "2s".
        if not _is_valid_duration(backup_retry_base):
            raise ConfigError(
                f"Invalid backup_retry_base: {backup_retry_base!r}. "
                "Must be a duration string like '1s', '5s', '10s'."
            )

        return TargetConfig(
            path=path,
            incremental=incremental,
            target_preserve=target_preserve,
            verify=verify,
            target_preserve_min=target_preserve_min,
            compress=compress,
            compression_type=compression_type,
            backup_stall_timeout=backup_stall_timeout,
            backup_retry_max=backup_retry_max,
            backup_retry_base=backup_retry_base,
        )

    # ── IConfigFacade implementation ──────────────────────────────────

    def get_global(self) -> GlobalConfig:
        return self._global

    def get_vms(self) -> list[VMConfig]:
        return list(self._vms)

    def get_vm(self, name: str) -> VMConfig:
        if name not in self._vm_map:
            raise KeyError(f"VM not found: {name!r}")
        return self._vm_map[name]
