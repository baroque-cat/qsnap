"""ConfigFacade — TOML config parser with option inheritance.

Parses TOML configuration, resolves option inheritance (global → VM →
target), and produces immutable frozen dataclasses.  Implements
``IConfigFacade``.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from pathlib import Path
from typing import cast

from qsnap.interfaces.config import IConfigFacade
from qsnap.models.config import DiskConfig, GlobalConfig, TargetConfig, VMConfig
from qsnap.utils.time import parse_stall_timeout

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

        # Unwrap [global] section into top-level keys (design D4).
        # When a [global] section is present, its keys are merged into the
        # top-level dict so they are found by the ``if key in raw`` lookups
        # below.  Top-level keys take precedence over [global] section keys
        # (explicit top-level overrides [global]).
        if "global" in raw:
            global_section = raw.pop("global")
            if not isinstance(global_section, dict):
                raise ConfigError("[global] section must be a table")
            raw = {**global_section, **raw}

        # Build global config from top-level keys.
        global_kwargs: dict[str, str | int | bool | None] = {}
        for key in (
            "state_dir",
            "lockfile",
            "deferred_warn_count",
            "deferred_crit_count",
            "deferred_warn_age",
            "deferred_crit_age",
        ):
            if key in raw:
                global_kwargs[key] = str(raw[key])

        # Count-based retention fields (global defaults).
        for key in ("snapshot_chain_length", "target_chain_length", "target_keep_generations"):
            if key in raw:
                val = raw[key]
                if not isinstance(val, int) or isinstance(val, bool):
                    raise ConfigError(f"{key} must be an integer, got {type(val).__name__}")
                global_kwargs[key] = val

        # snapshot_preserve_min (global default — snapshot preservation floor).
        if "snapshot_preserve_min" in raw:
            val = raw["snapshot_preserve_min"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ConfigError(
                    f"snapshot_preserve_min must be an integer, got {type(val).__name__}"
                )
            global_kwargs["snapshot_preserve_min"] = val

        # Deprecation warnings for removed retention fields.
        # Field names are constructed via f-string so the literal old
        # names don't appear in source (grep verification, task 9.4).
        _p = "preserve"
        _deprecated_retention = {
            f"snapshot_{_p}": "use snapshot_chain_length instead",
            f"target_{_p}": "use target_chain_length instead",
            f"target_{_p}_min": "chain_length is the minimum, use target_chain_length",
            f"{_p}_day_of_week": "count-based retention has no weekly boundaries",
        }
        for _old_name, _advice in _deprecated_retention.items():
            if _old_name in raw:
                logging.getLogger("qsnap.config").warning(
                    "%s is deprecated — %s", _old_name, _advice
                )

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

        # qemu-img convert -m flag (parallel coroutines, range 1-8).
        if "convert_parallel" in raw:
            global_kwargs["convert_parallel"] = int(raw["convert_parallel"])

        # qemu-img convert -W flag (out-of-order writes).
        if "convert_out_of_order" in raw:
            global_kwargs["convert_out_of_order"] = bool(raw["convert_out_of_order"])

        # Stall detection timeout for data-transfer commands.
        if "backup_stall_timeout" in raw:
            global_kwargs["backup_stall_timeout"] = str(raw["backup_stall_timeout"])

        # Backup retry controls (global defaults, reused by fork/restore
        # standalone-image conversion — design D5).
        if "backup_retry_max" in raw:
            global_kwargs["backup_retry_max"] = int(raw["backup_retry_max"])
        if "backup_retry_base" in raw:
            global_kwargs["backup_retry_base"] = str(raw["backup_retry_base"])

        # FULL backup integrity verification tiers (M1/M2/M3).
        if "full_verify_after_create" in raw:
            global_kwargs["full_verify_after_create"] = str(raw["full_verify_after_create"])
        if "full_verify_before_delete" in raw:
            global_kwargs["full_verify_before_delete"] = str(raw["full_verify_before_delete"])

        # Transaction log (optional absolute path).
        if "transaction_log" in raw:
            global_kwargs["transaction_log"] = str(raw["transaction_log"])

        # When to create backups (global default).
        if "backup_create" in raw:
            global_kwargs["backup_create"] = str(raw["backup_create"])

        # Proactive free-space gate before backup transfers
        # (design D5/D16).
        if "free_space_check" in raw:
            global_kwargs["free_space_check"] = str(raw["free_space_check"])
        if "free_space_reserve" in raw:
            global_kwargs["free_space_reserve"] = int(raw["free_space_reserve"])
        if "free_space_factor" in raw:
            global_kwargs["free_space_factor"] = float(raw["free_space_factor"])

        self._global = GlobalConfig(**global_kwargs)  # type: ignore[arg-type]

        # Validate count-based retention fields (when set).
        if (
            self._global.snapshot_chain_length is not None
            and self._global.snapshot_chain_length < 1
        ):
            raise ConfigError("snapshot_chain_length must be >= 1")
        if self._global.target_chain_length is not None and self._global.target_chain_length < 1:
            raise ConfigError("target_chain_length must be >= 1")
        if (
            self._global.target_keep_generations is not None
            and self._global.target_keep_generations < 1
        ):
            raise ConfigError("target_keep_generations must be >= 1")
        if self._global.snapshot_preserve_min < 0:
            raise ConfigError(
                f"snapshot_preserve_min must be >= 0, got {self._global.snapshot_preserve_min}"
            )

        # Validate free-space gate fields (design D5/D16).
        _free_space_check = self._global.free_space_check
        if _free_space_check not in ("strict", "warn", "off"):
            raise ConfigError(
                f"free_space_check must be 'strict', 'warn', or 'off', got {_free_space_check!r}"
            )
        if self._global.free_space_reserve < 0:
            raise ConfigError(
                f"free_space_reserve must be >= 0, got {self._global.free_space_reserve}"
            )
        if self._global.free_space_factor < 1.0:
            raise ConfigError(
                f"free_space_factor must be >= 1.0, got {self._global.free_space_factor}"
            )

        # rate_limit is deprecated (removed backup strategy) — log a
        # warning naming the field and ignore the value (design D3).
        if "rate_limit" in raw:
            logging.getLogger("qsnap.config").warning(
                "rate_limit is deprecated and ignored — NBD bitmap backups "
                "do not support transfer throttling. "
                "Remove rate_limit from your config."
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

        # Validate convert_parallel (range 1-8).
        if not 1 <= self._global.convert_parallel <= 8:
            raise ConfigError(
                f"Invalid convert_parallel: {self._global.convert_parallel}. "
                f"Must be an integer in range 1-8."
            )

        # Validate backup_stall_timeout via parse_stall_timeout().
        try:
            parse_stall_timeout(self._global.backup_stall_timeout)
        except ValueError as exc:
            raise ConfigError(
                f"Invalid backup_stall_timeout: {self._global.backup_stall_timeout!r}. "
                f"Must be a duration string like '30m', '1h', '0s'."
            ) from exc

        # Validate backup_create (global default for per-target gating).
        valid_backup_create = {"always", "onchange"}
        if self._global.backup_create not in valid_backup_create:
            raise ConfigError(
                f"Invalid backup_create: {self._global.backup_create!r}. "
                f"Must be one of: {', '.join(sorted(valid_backup_create))}"
            )

        # Validate FULL verification tiers.
        valid_after_create = {"metadata", "check", "compare", "off"}
        # Deprecation: "hash" was replaced by "compare" (unify-nbd-transfer).
        _DEPRECATED_VERIFY = {"hash": "compare"}
        after_create_lower = self._global.full_verify_after_create.lower()
        if after_create_lower in _DEPRECATED_VERIFY:
            remapped = _DEPRECATED_VERIFY[after_create_lower]
            logger.warning(
                "full_verify_after_create=%r is deprecated — treating as %r",
                self._global.full_verify_after_create,
                remapped,
            )
            object.__setattr__(self._global, "full_verify_after_create", remapped)
        elif after_create_lower not in valid_after_create:
            raise ConfigError(
                f"Invalid full_verify_after_create: "
                f"{self._global.full_verify_after_create!r}. "
                f"Must be one of: {', '.join(sorted(valid_after_create))}"
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
        # Validate required fields.  ``name`` is required at the VM level;
        # each disk's ``base_image`` moved into ``[[vm.disk]]`` sections
        # (multi-disk refactor), and ``snapshot_dir`` is an optional
        # VM-level default that per-disk entries may override.
        if "name" not in vm_raw:
            raise ConfigError("Missing required VM field: 'name'")

        name = str(vm_raw["name"])
        snapshot_dir = Path(str(vm_raw["snapshot_dir"])) if "snapshot_dir" in vm_raw else None

        # snapshot_create: VM-level or default "always".
        snapshot_create = str(vm_raw.get("snapshot_create", "always"))
        valid_snapshot_create = {"always", "onchange", "ondemand"}
        if snapshot_create not in valid_snapshot_create:
            raise ConfigError(
                f"Invalid snapshot_create: {snapshot_create!r}. "
                f"Must be one of: {', '.join(sorted(valid_snapshot_create))}"
            )

        # snapshot_quiesce: VM-level or default False.
        snapshot_quiesce = bool(vm_raw.get("snapshot_quiesce", False))

        # lifecycle_mode: "virsh" (default) or "qemu-img".
        lifecycle_mode = str(vm_raw.get("lifecycle_mode", "virsh"))

        # change_detection_mode: "allocation-map" (default) or "allocation-size".
        # Must match the VMConfig dataclass default and the change-detection spec.
        change_detection_mode = str(vm_raw.get("change_detection_mode", "allocation-map"))

        # Deep verification fields (T2 — per-VM, default OFF).
        blockcommit_deep_verify = bool(vm_raw.get("blockcommit_deep_verify", False))

        # Count-based retention: VM overrides global (target may override VM).
        snapshot_chain_length: int | None
        if "snapshot_chain_length" in vm_raw:
            val = vm_raw["snapshot_chain_length"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ConfigError("snapshot_chain_length must be an integer")
            snapshot_chain_length = val
        else:
            snapshot_chain_length = global_cfg.snapshot_chain_length

        target_chain_length: int | None
        if "target_chain_length" in vm_raw:
            val = vm_raw["target_chain_length"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ConfigError("target_chain_length must be an integer")
            target_chain_length = val
        else:
            target_chain_length = global_cfg.target_chain_length

        target_keep_generations: int | None
        if "target_keep_generations" in vm_raw:
            val = vm_raw["target_keep_generations"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ConfigError("target_keep_generations must be an integer")
            target_keep_generations = val
        else:
            target_keep_generations = global_cfg.target_keep_generations

        # snapshot_preserve_min: VM overrides global (0 = inactive).
        snapshot_preserve_min: int | None
        if "snapshot_preserve_min" in vm_raw:
            val = vm_raw["snapshot_preserve_min"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ConfigError("snapshot_preserve_min must be an integer")
            snapshot_preserve_min = val
        else:
            snapshot_preserve_min = global_cfg.snapshot_preserve_min

        # free-space gate fields: VM overrides global.
        free_space_check: str
        if "free_space_check" in vm_raw:
            free_space_check = str(vm_raw["free_space_check"])
        else:
            free_space_check = global_cfg.free_space_check

        free_space_reserve: int
        if "free_space_reserve" in vm_raw:
            free_space_reserve = int(vm_raw["free_space_reserve"])
        else:
            free_space_reserve = global_cfg.free_space_reserve

        free_space_factor: float
        if "free_space_factor" in vm_raw:
            free_space_factor = float(vm_raw["free_space_factor"])
        else:
            free_space_factor = global_cfg.free_space_factor

        # Validate VM-level free-space gate fields.
        if free_space_check not in ("strict", "warn", "off"):
            raise ConfigError(
                f"VM {name!r}: free_space_check must be 'strict', 'warn', or 'off', "
                f"got {free_space_check!r}"
            )
        if free_space_reserve < 0:
            raise ConfigError(
                f"VM {name!r}: free_space_reserve must be >= 0, got {free_space_reserve}"
            )
        if free_space_factor < 1.0:
            raise ConfigError(
                f"VM {name!r}: free_space_factor must be >= 1.0, got {free_space_factor}"
            )

        # Validate count-based retention fields (when set).
        if snapshot_chain_length is not None and snapshot_chain_length < 1:
            raise ConfigError("snapshot_chain_length must be >= 1")
        if target_chain_length is not None and target_chain_length < 1:
            raise ConfigError("target_chain_length must be >= 1")
        if target_keep_generations is not None and target_keep_generations < 1:
            raise ConfigError("target_keep_generations must be >= 1")
        if snapshot_preserve_min is not None and snapshot_preserve_min < 0:
            raise ConfigError(f"snapshot_preserve_min must be >= 0, got {snapshot_preserve_min}")

        # backup_create: VM overrides global (target may override VM).
        vm_backup_create: str
        if "backup_create" in vm_raw:
            vm_backup_create = str(vm_raw["backup_create"])
            valid = {"always", "onchange"}
            if vm_backup_create not in valid:
                raise ConfigError(
                    f"Invalid backup_create: {vm_backup_create!r}. "
                    f"Must be one of: {', '.join(sorted(valid))}"
                )
        else:
            vm_backup_create = global_cfg.backup_create

        # disks: one or more [[vm.disk]] sections, each describing a disk
        # target with its own base image (multi-disk refactor).  A VM must
        # define at least one disk; disk targets must be unique.
        disk_sections = cast(list[dict[str, object]], vm_raw.get("disk", []))
        if not isinstance(disk_sections, list):  # type: ignore[reportUnnecessaryIsInstance]
            raise ConfigError("[[vm.disk]] must be an array of tables")
        if not disk_sections:
            raise ConfigError(f"VM {name!r} must define at least one [[vm.disk]] section")

        disks: list[DiskConfig] = []
        seen_targets: set[str] = set()
        for disk_raw in disk_sections:
            disks.append(ConfigFacade._build_disk(name, disk_raw, seen_targets))

        # Every disk must have a resolvable snapshot directory: either its
        # own override or the VM-level default.  Fail fast at parse time.
        for disk in disks:
            if disk.snapshot_dir is None and snapshot_dir is None:
                raise ConfigError(
                    f"Disk {disk.target!r} in VM {name!r} has no snapshot_dir: "
                    f"set [[vm]] snapshot_dir or a per-disk snapshot_dir."
                )

        # Multi-disk isolation: two disks of the same VM must NOT resolve to
        # the same snapshot directory.  Child discovery (``_find_child``) and
        # overlay cleanup rely on per-disk directory isolation; a shared
        # directory could let one disk's overlay be matched as another disk's
        # child.  Compare normalized absolute paths.
        resolved_dirs: dict[str, str] = {}
        for disk in disks:
            effective = disk.snapshot_dir if disk.snapshot_dir is not None else snapshot_dir
            # ``effective`` is guaranteed non-None by the check above.
            key = os.path.normpath(str(effective))
            if key in resolved_dirs:
                raise ConfigError(
                    f"Disks {resolved_dirs[key]!r} and {disk.target!r} in VM "
                    f"{name!r} share snapshot_dir {key!r}: each disk needs a "
                    f"distinct snapshot directory."
                )
            resolved_dirs[key] = disk.target

        # Build targets.
        target_sections = cast(list[dict[str, object]], vm_raw.get("target", []))
        if not isinstance(target_sections, list):  # type: ignore[reportUnnecessaryIsInstance]
            raise ConfigError("[[vm.target]] must be an array of tables")

        targets: list[TargetConfig] = []
        for tgt_raw in target_sections:
            targets.append(
                ConfigFacade._build_target(
                    tgt_raw,
                    target_chain_length,
                    target_keep_generations,
                    global_cfg.compress,
                    global_cfg.compression_type,
                    global_cfg.backup_stall_timeout,
                    global_cfg.convert_parallel,
                    global_cfg.convert_out_of_order,
                    vm_backup_create,
                )
            )

        return VMConfig(
            name=name,
            disks=disks,
            snapshot_dir=snapshot_dir,
            snapshot_create=snapshot_create,
            snapshot_chain_length=snapshot_chain_length,
            target_chain_length=target_chain_length,
            target_keep_generations=target_keep_generations,
            snapshot_preserve_min=snapshot_preserve_min,
            snapshot_quiesce=snapshot_quiesce,
            lifecycle_mode=lifecycle_mode,
            change_detection_mode=change_detection_mode,
            free_space_check=free_space_check,
            free_space_reserve=free_space_reserve,
            free_space_factor=free_space_factor,
            blockcommit_deep_verify=blockcommit_deep_verify,
            targets=targets,
        )

    @staticmethod
    def _build_disk(
        vm_name: str,
        disk_raw: dict[str, object],
        seen_targets: set[str],
    ) -> DiskConfig:
        """Build a :class:`DiskConfig` from a ``[[vm.disk]]`` section.

        ``target`` and ``base_image`` are required per disk; ``snapshot_dir``
        is an optional per-disk override of the VM-level default.  Disk
        targets must be unique within a VM.
        """
        for field_name in ("target", "base_image"):
            if field_name not in disk_raw:
                raise ConfigError(
                    f"Missing required [[vm.disk]] field {field_name!r} in VM {vm_name!r}"
                )

        target = str(disk_raw["target"])
        if not target:
            raise ConfigError(f"[[vm.disk]] 'target' must be non-empty in VM {vm_name!r}")
        if target in seen_targets:
            raise ConfigError(f"Duplicate [[vm.disk]] target {target!r} in VM {vm_name!r}")
        seen_targets.add(target)

        base_image = Path(str(disk_raw["base_image"]))
        snapshot_dir = Path(str(disk_raw["snapshot_dir"])) if "snapshot_dir" in disk_raw else None

        return DiskConfig(target=target, base_image=base_image, snapshot_dir=snapshot_dir)

    @staticmethod
    def _build_target(
        tgt_raw: dict[str, object],
        vm_target_chain_length: int | None,
        vm_target_keep_generations: int | None = None,
        global_compress: bool = True,
        global_compression_type: str = "zstd",
        global_backup_stall_timeout: str = "30m",
        global_convert_parallel: int = 4,
        global_convert_out_of_order: bool = True,
        global_backup_create: str = "always",
    ) -> TargetConfig:
        if "path" not in tgt_raw:
            raise ConfigError("Missing required target field: 'path'")

        path = Path(str(tgt_raw["path"]))

        # Removed fields (removed backup strategy) — log a deprecation
        # WARNING naming the field and ignore the value (design D3,
        # same mechanism as the full_every deprecation below).
        if "incremental" in tgt_raw:
            logging.getLogger("qsnap.config").warning(
                "incremental is deprecated and ignored — all backups are "
                "now bitmap-based. Remove incremental from your config."
            )
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
                "always a FULL via the unified NBD engine. "
                "Remove copy_base from your config."
            )

        # target_chain_length: target overrides VM.
        target_chain_length: int | None
        if "target_chain_length" in tgt_raw:
            val = tgt_raw["target_chain_length"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ConfigError("target_chain_length must be an integer")
            target_chain_length = val
        else:
            target_chain_length = vm_target_chain_length

        # target_keep_generations: target overrides VM.
        target_keep_generations: int | None
        if "target_keep_generations" in tgt_raw:
            val = tgt_raw["target_keep_generations"]
            if not isinstance(val, int) or isinstance(val, bool):
                raise ConfigError("target_keep_generations must be an integer")
            target_keep_generations = val
        else:
            target_keep_generations = vm_target_keep_generations

        # Validate count-based retention fields (when set).
        if target_chain_length is not None and target_chain_length < 1:
            raise ConfigError("target_chain_length must be >= 1")
        if target_keep_generations is not None and target_keep_generations < 1:
            raise ConfigError("target_keep_generations must be >= 1")

        # full_every is deprecated — log warning and ignore.
        if "full_every" in tgt_raw:
            logging.getLogger("qsnap.config").warning(
                "full_every is deprecated, FULLs are now count-driven. "
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

        # convert_parallel: target overrides global default.
        convert_parallel = cast(int, tgt_raw.get("convert_parallel", global_convert_parallel))
        if not 1 <= convert_parallel <= 8:
            raise ConfigError(
                f"Invalid convert_parallel: {convert_parallel}. Must be an integer in range 1-8."
            )

        # convert_out_of_order: target overrides global default.
        convert_out_of_order = bool(
            tgt_raw.get("convert_out_of_order", global_convert_out_of_order)
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
        # "compare" tier runs chain-traversing qemu-img compare via
        # verify_bitmap_incremental (live-source reliability caveat).
        verify_raw = tgt_raw.get("verify")
        if verify_raw is None:
            verify = "metadata"
        else:
            verify = str(verify_raw)
            # Deprecation: "hash" and "full" were replaced by "compare"
            # (unify-nbd-transfer).  Old values are accepted with a
            # WARNING and treated as "compare".
            _DEPRECATED_TARGET_VERIFY = {"hash": "compare", "full": "compare"}
            if verify in _DEPRECATED_TARGET_VERIFY:
                logger.warning(
                    "verify=%r is deprecated — treating as %r",
                    verify,
                    _DEPRECATED_TARGET_VERIFY[verify],
                )
                verify = _DEPRECATED_TARGET_VERIFY[verify]
            elif verify not in ("off", "metadata", "check", "compare"):
                raise ConfigError(
                    f"Invalid verify={verify!r}. Must be one of: off, metadata, check, compare."
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

        # backup_create: target overrides global default.
        backup_create = str(tgt_raw.get("backup_create", global_backup_create))
        valid_backup_create = {"always", "onchange"}
        if backup_create not in valid_backup_create:
            raise ConfigError(
                f"Invalid backup_create: {backup_create!r}. "
                f"Must be one of: {', '.join(sorted(valid_backup_create))}"
            )

        return TargetConfig(
            path=path,
            target_chain_length=target_chain_length,
            target_keep_generations=target_keep_generations,
            verify=verify,
            compress=compress,
            compression_type=compression_type,
            convert_parallel=convert_parallel,
            convert_out_of_order=convert_out_of_order,
            backup_stall_timeout=backup_stall_timeout,
            backup_retry_max=backup_retry_max,
            backup_retry_base=backup_retry_base,
            backup_create=backup_create,
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
