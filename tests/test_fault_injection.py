import csv
import io
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import warnings
import zipfile

import pytest

from scenepack import GRIDS, PROJ_DATA
from scenepack.model import canonical, file_hash, read_json, sha256, write_json
from scenepack.transform import receipt_identity
from scenepack.verify import extract
from conftest import FIXTURES, make_scene, run
from test_known_answers import assert_known_horizontal


def rewrite(path, name, change, *, rehash=False):
    with zipfile.ZipFile(path) as archive:
        entries = {n: archive.read(n) for n in archive.namelist()}
    replacement = change(entries[name])
    if replacement is None:
        del entries[name]
    else:
        entries[name] = replacement
    if rehash:
        manifest = json.loads(entries["MANIFEST.json"])
        manifest["files"] = {n: sha256(b) for n, b in entries.items() if n != "MANIFEST.json"}
        entries["MANIFEST.json"] = canonical(manifest).encode()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for n, b in entries.items():
            archive.writestr(n, b)


def test_axis_swap_detectable_by_known_answers(tmp_path):
    rows = list(csv.reader((FIXTURES / "slc-synthetic-ftus.csv").open()))
    for row in rows:
        row[1], row[2] = row[2], row[1]
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    path = tmp_path / "penzd.csv"
    path.write_text(stream.getvalue())
    root = make_scene(tmp_path / "swapped", csv_path=path)
    # A reversed State Plane coordinate can also fall outside the grid entirely.
    # The receipt-free horizontal known-answer check must catch the axis swap.
    import pyproj
    horizontal = pyproj.Transformer.from_crs("EPSG:6625", "EPSG:4326", always_xy=True)
    points = read_json(root / "points.json")["points"]
    derived = {"derived": []}
    for p in points[:3]:
        lon, lat = horizontal.transform(p["e"], p["n"])
        derived["derived"].append(dict(lon=lon, lat=lat))
    with pytest.raises(AssertionError):
        assert_known_horizontal(derived)
    correct = make_scene(tmp_path / "correct", csv_path=path, order="PENZD")
    assert run("transform", correct) == 0
    assert_known_horizontal(read_json(correct / "transform/derived.json"))


def test_tampered_raw(packed, capsys):
    name = "raw/slc-synthetic-ftus.csv"
    rewrite(packed, name, lambda b: b.replace(b"7428000.0", b"7428001.0", 1))
    assert run("verify", packed) == 2
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "PRESERVATION FAIL" in output and name in output


def test_rehashed_derived(packed, capsys):
    def mutate(data):
        obj = json.loads(data)
        obj["derived"][4]["h_egm96_m"] += 1.0
        return canonical(obj).encode()
    rewrite(packed, "transform/derived.json", mutate, rehash=True)
    assert run("verify", packed) == 2
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "PRESERVATION PASS" in output
    assert "RECOMPUTATION FAIL" in output and "p0005: coordinate residual" in output


def test_grid_swapped(packed, capsys):
    name = "us_noaa_g2018u0.tif"
    with zipfile.ZipFile(packed) as archive:
        replacement = archive.read("transform/grids/us_nga_egm96_15.tif")
    rewrite(packed, f"transform/grids/{name}", lambda _: replacement, rehash=True)
    assert run("verify", packed) == 2
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert f"receipt grid hash mismatch: {name}" in output
    assert "RECOMPUTATION FAIL" in output


def test_grid_deleted(packed, capsys):
    capsys.readouterr()
    name = "us_noaa_g2018u0.tif"
    rewrite(packed, f"transform/grids/{name}", lambda _: None)
    assert run("verify", packed) == 2
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert f"MISSING grid {name}" in output
    assert "RECOMPUTATION FAIL" in output
    assert "us_nga_" not in output
    assert ".tif" not in output.replace(name, "")


def verify_with_host_grid(packed, tmp_path, grid_bytes):
    host = tmp_path / "host-proj"
    host.mkdir()
    shutil.copyfile(Path(PROJ_DATA) / "proj.db", host / "proj.db")
    (host / "us_noaa_g2018u0.tif").write_bytes(grid_bytes)
    code = """
import sys
import pyproj
import scenepack.verify as verifier
pyproj.datadir.set_data_dir(sys.argv[2])
verifier.PROJ_DATA = sys.argv[2]
sys.exit(verifier.verify_package(sys.argv[1]))
"""
    return subprocess.run([sys.executable, "-c", code, str(packed), str(host)],
                          capture_output=True, text=True, close_fds=False)


def test_shadowed_grid_is_refused(packed, tmp_path):
    root = packed.parent
    grid_path = root / "transform/grids/us_noaa_g2018u0.tif"
    grid_path.write_bytes((GRIDS / "us_nga_egm96_15.tif").read_bytes())
    receipt = read_json(root / "transform/receipt.json")
    for grid in receipt["grids"]:
        if grid["name"] == grid_path.name:
            grid["sha256"] = file_hash(grid_path)
    receipt["receipt_id"] = receipt_identity(receipt)
    derived = read_json(root / "transform/derived.json")
    derived["receipt_id"] = receipt["receipt_id"]
    write_json(root / "transform/derived.json", derived)
    receipt["output_sha256"] = file_hash(root / "transform/derived.json")
    write_json(root / "transform/receipt.json", receipt)
    assert run("pack", root) == 0
    result = verify_with_host_grid(packed, tmp_path, (GRIDS / grid_path.name).read_bytes())
    output = result.stdout + result.stderr
    assert result.returncode == 2, output
    assert "PRESERVATION PASS" in output and "RECOMPUTATION FAIL" in output
    assert "coordinate residual" in output or "grid" in output
    assert "RECOMPUTATION PASS" not in output


def test_package_grid_wins_over_corrupt_host_grid(packed, tmp_path):
    result = verify_with_host_grid(packed, tmp_path, b"corrupt host grid")
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "PRESERVATION PASS" in output and "RECOMPUTATION PASS" in output
    assert "BYTE-IDENTICAL yes" in output


def test_transform_refuses_grid_outside_trusted_directory(scene, tmp_path):
    host = tmp_path / "host-grids"
    shutil.copytree(GRIDS, host)
    code = """
import os
import sys
import pyproj
from scenepack import PROJ_DATA
from scenepack.cli import main
pyproj.datadir.set_data_dir(f"{sys.argv[2]}{os.pathsep}{PROJ_DATA}")
sys.exit(main(["transform", sys.argv[1]]))
"""
    result = subprocess.run([sys.executable, "-c", code, str(scene), str(host)],
                            capture_output=True, text=True, close_fds=False)
    assert result.returncode == 2, result.stdout + result.stderr
    assert f"REFUSED: grid resolved outside the trusted directory: {host}/" in result.stderr
    assert not (scene / "transform/receipt.json").exists()


def test_stale_export(packed, capsys):
    rewrite(packed, "exports/scene.kml", lambda b: b + b"<!-- edited -->")
    assert run("verify", packed) == 2
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "PRESERVATION FAIL" in output and "exports/scene.kml" in output
    assert "RECOMPUTATION PASS" in output


@pytest.mark.parametrize("name", ["../x", "/absolute", "a/../../x", "C:/x", "a\\..\\x", "a/./x"])
def test_unsafe_zip_before_extraction(tmp_path, capsys, name):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("innocent", "first")
        archive.writestr(name, "bad")
    target = tmp_path / "extract"
    target.mkdir()
    with pytest.raises(ValueError, match="unsafe ZIP path"):
        extract(path, target)
    assert list(target.iterdir()) == []
    assert run("verify", path) == 2
    output = capsys.readouterr()
    assert "REFUSED: unsafe ZIP path" in output.err
    assert output.out.rstrip().endswith("independently recorded copy.")


def test_duplicate_zip(tmp_path, capsys):
    path = tmp_path / "duplicate.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("same", "one")
            archive.writestr("same", "two")
    assert run("verify", path) == 2
    assert "REFUSED: duplicate ZIP entry: same" in capsys.readouterr().err


def test_symlink_zip(tmp_path, capsys):
    path = tmp_path / "link.zip"
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, "../../escape")
    assert run("verify", path) == 2
    assert "REFUSED: symlink ZIP entry" in capsys.readouterr().err


def test_pipeline_differs(packed, capsys):
    from scenepack.transform import receipt_identity
    def mutate(data):
        receipt = json.loads(data)
        receipt["pipeline"] += " +different=true"
        receipt["receipt_id"] = receipt_identity(receipt)
        return canonical(receipt).encode()
    rewrite(packed, "transform/receipt.json", mutate, rehash=True)
    assert run("verify", packed) == 2
    assert "pipeline differs" in capsys.readouterr().err


def test_unlisted_file(packed, capsys):
    with zipfile.ZipFile(packed, "a") as archive:
        archive.writestr("extra.txt", "unlisted")
    assert run("verify", packed) == 2
    assert "UNLISTED extra.txt" in capsys.readouterr().err
