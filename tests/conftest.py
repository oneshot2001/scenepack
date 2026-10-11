from pathlib import Path

import pyproj
import pytest

from scenepack.cli import main
from scenepack.model import load_scene, save_scene

FIXTURES = Path(__file__).parent / "fixtures"


def run(*args):
    return main([str(a) for a in args])


def make_scene(root, *, metres=False, tied=True, csv_path=None, order="PNEZD"):
    assert run("init", root) == 0
    assert run("import", root, csv_path or FIXTURES / f"slc-synthetic-{'m' if metres else 'ftus'}.csv",
               "--order", order, "--h-unit", "m" if metres else "ftUS",
               "--v-unit", "m" if metres else "ftUS",
               "--h-crs", "EPSG:6619" if metres else "EPSG:6625",
               "--v-crs", "EPSG:5703" if metres else "EPSG:6360", "--class", "synthetic") == 0
    if tied:
        scene = load_scene(root)
        scene.case_label = "Synthetic SLC test"
        scene.control_tie["mode"] = "tied"
        scene.control_tie["method"] = "Synthetic coordinates in the declared CRS; no field control claim"
        scene.control_tie["control_points"] = [dict(id="CP1", published_n=7428000.0,
            published_e=1551000.0, published_z=4260.0, datum="NAD83(2011)/NAVD88", epoch="2010.00")]
        save_scene(root, scene)
    return root


@pytest.fixture(autouse=True)
def offline():
    assert pyproj.network.is_network_enabled() is False
    yield
    assert pyproj.network.is_network_enabled() is False


@pytest.fixture
def scene(tmp_path):
    return make_scene(tmp_path / "scene")


@pytest.fixture
def packed(scene):
    assert run("transform", scene) == 0
    assert run("export-kml", scene) == 0
    assert run("pack", scene) == 0
    return scene / "pack.zip"
