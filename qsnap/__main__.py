"""Allow running qsnap as ``python -m qsnap``."""

from __future__ import annotations

import sys

from qsnap.cli.app import main

if __name__ == "__main__":
    sys.exit(main())
