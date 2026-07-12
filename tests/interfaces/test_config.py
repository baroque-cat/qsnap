"""Contract test: ConfigFacade implements IConfigFacade."""

from __future__ import annotations

from pathlib import Path

from qsnap.config.facade import ConfigFacade
from qsnap.interfaces.config import IConfigFacade

CONFIG_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "configs" / "minimal.toml"


def test_config_facade_is_iconfigfacade():
    """ConfigFacade is an instance of IConfigFacade."""
    facade = ConfigFacade(CONFIG_PATH)
    assert isinstance(facade, IConfigFacade)
