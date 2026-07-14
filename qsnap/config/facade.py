"""ConfigFacade — TOML config parser with option inheritance.

Parses TOML configuration, resolves option inheritance (global → VM →
target), and produces immutable frozen dataclasses.  Implements
``IConfigFacade``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from qsnap.interfaces.config import IConfigFacade
from qsnap.models.config import GlobalConfig, TargetConfig, VMConfig


class ConfigError(Exception):
    """Raised when the configuration is invalid (malformed TOML, missing fields)."""


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
        global_kwargs: dict[str, str | None] = {}
        for key in (
            "timestamp_format",
            "preserve_day_of_week",
            "state_dir",
            "lockfile",
            "snapshot_preserve",
            "target_preserve",
            "snapshot_preserve_min",
            "target_preserve_min",
        ):
            if key in raw:
                global_kwargs[key] = str(raw[key])
        self._global = GlobalConfig(**global_kwargs)  # type: ignore[arg-type]

        # Validate preserve_day_of_week.
        valid_days = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
        if self._global.preserve_day_of_week.lower() not in valid_days:
            raise ConfigError(
                f"Invalid preserve_day_of_week: {self._global.preserve_day_of_week!r}. "
                f"Must be one of: {', '.join(sorted(valid_days))}"
            )

        # Build VM configs.
        vm_sections = raw.get("vm", [])
        if not isinstance(vm_sections, list):
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
        change_detection_mode = str(
            vm_raw.get("change_detection_mode", "allocation-size")
        )

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
        target_sections = vm_raw.get("target", [])
        if not isinstance(target_sections, list):
            raise ConfigError("[[vm.target]] must be an array of tables")

        targets: list[TargetConfig] = []
        for tgt_raw in target_sections:
            targets.append(
                ConfigFacade._build_target(tgt_raw, target_preserve, target_preserve_min)
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
            targets=targets,
        )

    @staticmethod
    def _build_target(
        tgt_raw: dict[str, object],
        vm_target_preserve: str | None,
        vm_target_preserve_min: str | None = None,
    ) -> TargetConfig:
        if "path" not in tgt_raw:
            raise ConfigError("Missing required target field: 'path'")

        path = Path(str(tgt_raw["path"]))
        incremental = bool(tgt_raw.get("incremental", True))
        incremental_mode = str(tgt_raw.get("incremental_mode", "file-copy"))

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

        # full_every: target-level, default "0d" (never).
        full_every = str(tgt_raw.get("full_every", "0d"))

        # full_compress: target-level, default False.
        full_compress = bool(tgt_raw.get("full_compress", False))

        # verify: "metadata" (default), "hash", "full", or "off".
        verify = str(tgt_raw.get("verify", "metadata"))

        return TargetConfig(
            path=path,
            incremental=incremental,
            incremental_mode=incremental_mode,
            target_preserve=target_preserve,
            verify=verify,
            target_preserve_min=target_preserve_min,
            full_every=full_every,
            full_compress=full_compress,
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
