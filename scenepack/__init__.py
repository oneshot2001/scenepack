"""Offline survey-point evidence packages."""

import os
import warnings
from pathlib import Path

os.environ["PROJ_NETWORK"] = "OFF"

import pyproj  # noqa: E402

__version__ = "0.1.0"
PROJ_DATA = pyproj.datadir.get_data_dir()
GRIDS = Path(__file__).parent / "grids"


def set_search_path(trusted_dir):
    """Trusted grid dir first so PROJ reads the grid we hash; PROJ's own dir (proj.db) second.

    pyproj warns that the first entry holds no proj.db; the second one does, so the warning is noise.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="pyproj unable to set PROJ database path")
        pyproj.datadir.set_data_dir(f"{trusted_dir}{os.pathsep}{PROJ_DATA}")


set_search_path(GRIDS)
pyproj.network.set_network_enabled(False)
