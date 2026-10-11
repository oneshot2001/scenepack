import pytest
from types import SimpleNamespace

from scenepack.model import Refused, load_scene, save_scene
from scenepack.transform import build_transformer
from conftest import FIXTURES, make_scene, run


def test_ballpark_transform_refused(tmp_path, capsys):
    root = tmp_path / "ballpark"
    assert run("init", root) == 0
    assert run("import", root, FIXTURES / "slc-synthetic-m.csv", "--order", "PNEZD",
               "--h-unit", "m", "--v-unit", "m", "--h-crs", "EPSG:25832", "--v-crs", "EPSG:5703") == 0
    scene = load_scene(root)
    scene.control_tie["mode"] = "tied"
    save_scene(root, scene)
    assert run("transform", root) == 2
    error = capsys.readouterr().err
    assert error.startswith("REFUSED:") and "ballpark" in error
    assert not (root / "transform/derived.json").exists()


@pytest.mark.parametrize("step,description,accuracy,ballpark", [
    ("noop", "operation", 1, False),
    ("ballpark", "operation", 1, False),
    ("pipeline", "Ballpark geographic offset", 1, False),
    ("pipeline", "operation", None, False),
    ("pipeline", "operation", -1, False),
    ("pipeline", "operation", 1, True),
])
def test_unacceptable_operation_refused(monkeypatch, step, description, accuracy, ballpark):
    chosen = SimpleNamespace(description=description, accuracy=accuracy,
        operations=[SimpleNamespace(has_ballpark_transformation=ballpark)],
        to_proj4=lambda: f"+proj={step} +grids=us_noaa_g2018u0.tif,us_nga_egm96_15.tif")
    def group(*args, **kwargs):
        assert kwargs["allow_ballpark"] is False
        return SimpleNamespace(transformers=[chosen])
    monkeypatch.setattr("scenepack.transform.TransformerGroup", group)
    with pytest.raises(Refused, match="unacceptable operation"):
        build_transformer("EPSG:6625+6360")


@pytest.mark.parametrize("h_crs,h_unit,v_crs,v_unit,cause", [
    ("EPSG:2281", "ftUS", "EPSG:6360", "ftUS", "declares foot, contradicting ftUS"),
    ("EPSG:6625", "m", "EPSG:6360", "ftUS", "US survey foot"),
    ("EPSG:6625", "ftUS", "EPSG:5703", "ftUS", "metre"),
    ("EPSG:7407", "ftUS", "EPSG:6360", "ftUS", "projected horizontal CRS required"),
    ("EPSG:6625", "ftUS", "EPSG:7407", "ftUS", "vertical CRS required"),
    ("EPSG:6625", "ftUS", "EPSG:5703", "m", "mixed horizontal and vertical units"),
    ("EPSG:6619", "m", "EPSG:6360", "ftUS", "mixed horizontal and vertical units"),
])
def test_unit_refusals(tmp_path, capsys, h_crs, h_unit, v_crs, v_unit, cause):
    root = tmp_path / "scene"
    assert run("init", root) == 0
    assert run("import", root, FIXTURES / "slc-synthetic-ftus.csv", "--order", "PNEZD",
               "--h-unit", h_unit, "--v-unit", v_unit, "--h-crs", h_crs, "--v-crs", v_crs) == 2
    error = capsys.readouterr().err
    assert error.startswith("REFUSED:") and cause in error
    assert not (root / "points.json").exists()
    assert list((root / "raw").iterdir()) == []


@pytest.mark.parametrize("row", ["P,NaN,1,2,CP", "P,1,inf,2,CP", "P,1,2,-inf,CP", "P,bad,2,3,CP", "P,1,bad,3,CP", "P,1,2,bad,CP",
                                "P,7_428_000,1,2,CP", "P,1, 5 ,2,CP", "P,1,2, 5 ,CP", "P,1,2, ,CP", "P,1e999,2,3,CP"])
def test_invalid_numbers(tmp_path, capsys, row):
    root = tmp_path / "scene"
    csv = tmp_path / "bad.csv"
    csv.write_text("P,N,E,Z,D\n" + row + "\n")
    assert run("init", root) == 0
    assert run("import", root, csv, "--order", "PNEZD", "--h-unit", "ftUS", "--v-unit", "ftUS",
               "--h-crs", "EPSG:6625", "--v-crs", "EPSG:6360") == 2
    assert "REFUSED: row 2" in capsys.readouterr().err


@pytest.mark.parametrize("row", ["CP1,N/A,N/A,4260,CP", "1,N/A,N/A,N/A,CP", "CP1,N/A,N/A,N/A,1"])
def test_invalid_first_row_is_not_header(tmp_path, capsys, row):
    root = tmp_path / "scene"
    csv = tmp_path / "bad-header.csv"
    csv.write_text(row + "\nCP2,7428000,1551000,4260,CP\n")
    assert run("init", root) == 0
    assert run("import", root, csv, "--order", "PNEZD", "--h-unit", "ftUS", "--v-unit", "ftUS",
               "--h-crs", "EPSG:6625", "--v-crs", "EPSG:6360") == 2
    assert "REFUSED: row 1" in capsys.readouterr().err
    assert not (root / "points.json").exists()


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_line_refused(tmp_path, capsys, blank):
    root = tmp_path / "scene"
    csv = tmp_path / "blank.csv"
    csv.write_text("P,N,E,Z,D\n" + blank + "\nCP1,7428000,1551000,4260,CP\n")
    assert run("init", root) == 0
    assert run("import", root, csv, "--order", "PNEZD", "--h-unit", "ftUS", "--v-unit", "ftUS",
               "--h-crs", "EPSG:6625", "--v-crs", "EPSG:6360") == 2
    assert "REFUSED: row 2: blank line" in capsys.readouterr().err


def test_local_only(tmp_path, capsys):
    root = make_scene(tmp_path / "local", tied=False)
    assert run("transform", root) == 2
    assert "REFUSED: local-only" in capsys.readouterr().err
    assert run("pack", root) == 0
    assert "Proves: Nothing beyond file hashes: internal_consistency = not_evaluated." in (root / "COVER.md").read_text()
    assert run("verify", root / "pack.zip") == 0
    output = capsys.readouterr().out
    assert "PRESERVATION PASS" in output
    assert "RECOMPUTATION SKIPPED(no transform)" in output


def test_second_import(scene, capsys):
    original = (scene / "points.json").read_bytes()
    assert run("import", scene, FIXTURES / "slc-synthetic-ftus.csv", "--order", "PNEZD",
               "--h-unit", "ftUS", "--v-unit", "ftUS", "--h-crs", "EPSG:6625", "--v-crs", "EPSG:6360") == 2
    assert "REFUSED: second import" in capsys.readouterr().err
    assert (scene / "points.json").read_bytes() == original


def test_unknown_mode(scene, capsys):
    config = load_scene(scene)
    config.control_tie["mode"] = "unknown"
    save_scene(scene, config)
    assert run("transform", scene) == 2
    assert "REFUSED: unknown control_tie.mode" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["nan", "inf", "-1"])
def test_invalid_tolerance(tmp_path, capsys, value):
    assert run("verify", tmp_path / "unused.zip", f"--tolerance-m={value}") == 2
    output = capsys.readouterr()
    assert "REFUSED: tolerance" in output.err
    assert output.out.rstrip().endswith("independently recorded copy.")


def test_no_vertical_grids(tmp_path):
    import shutil
    import subprocess
    import sys
    from scenepack import PROJ_DATA

    root = make_scene(tmp_path / "scene")
    isolated = tmp_path / "proj"
    isolated.mkdir()
    from pathlib import Path
    shutil.copyfile(Path(PROJ_DATA) / "proj.db", isolated / "proj.db")
    code = """
import sys
import pyproj
from scenepack.cli import main
pyproj.datadir.set_data_dir(sys.argv[2])
sys.exit(main(["transform", sys.argv[1]]))
"""
    result = subprocess.run([sys.executable, "-c", code, str(root), str(isolated)],
                            capture_output=True, text=True, close_fds=False)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSED:" in result.stderr
    assert "us_noaa_g2018u0.tif" in result.stderr
    assert not (root / "transform/derived.json").exists()
