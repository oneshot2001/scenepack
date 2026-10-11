from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

import pytest

from scenepack import GRIDS, PROJ_DATA
from scenepack.kml import DISCLAIMER, NS
from scenepack.model import Limits, Manifest, Point, Receipt, Scene, file_hash, read_json, write_json
from scenepack.transform import receipt_identity
from conftest import FIXTURES, make_scene, run


def test_pack_verify(packed, tmp_path, capsys):
    work = tmp_path / "work"
    work.mkdir()
    (work / "keep").write_text("existing file")
    assert run("verify", packed, "--work", work) == 0
    output = capsys.readouterr().out
    assert "PRESERVATION PASS" in output
    assert "RECOMPUTATION PASS" in output
    assert "BYTE-IDENTICAL yes" in output
    assert "p0040 |Δlon|=" in output
    assert list(work.iterdir()) == [work / "keep"]
    receipt = read_json(packed.parent / "transform/receipt.json")
    assert receipt["receipt_id"] == receipt_identity(receipt)
    assert receipt["output_sha256"] == file_hash(packed.parent / "transform/derived.json")
    assert receipt["input_sha256"] == file_hash(packed.parent / "points.json")
    assert receipt["proj_db_sha256"] == file_hash(Path(PROJ_DATA) / "proj.db")
    assert receipt["code_sha256"] == file_hash(Path(__file__).parents[1] / "scenepack/transform.py")
    for grid in receipt["grids"]:
        assert grid["resolved_path"] == grid["name"]
        assert grid["sha256"] == file_hash(GRIDS / grid["resolved_path"])
    with zipfile.ZipFile(packed) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in archive.infolist())
        manifest = json.loads(archive.read("MANIFEST.json"))
        assert set(manifest["files"]) == set(archive.namelist()) - {"MANIFEST.json"}
        assert manifest["limits"]["internal_consistency"] == "satisfied"
        assert all(v == "not_established" for k, v in manifest["limits"].items() if k != "internal_consistency")
        assert "{{" not in archive.read("COVER.md").decode()
        assert "every file agrees with the included manifest (subject to the independent digest comparison above)" in archive.read("COVER.md").decode()


def test_pack_twice_identical(packed):
    before = packed.read_bytes()
    (packed.parent / "COVER.md").write_text("stale generated cover")
    assert run("pack", packed.parent) == 0
    assert packed.read_bytes() == before


def test_wholesale_rewrite(packed, tmp_path, capsys):
    csv = tmp_path / "rewrite.csv"
    csv.write_bytes((FIXTURES / "slc-synthetic-ftus.csv").read_bytes().replace(b"7428000.0", b"7428002.0", 1))
    root = make_scene(tmp_path / "new-scene", csv_path=csv)
    assert run("transform", root) == run("export-kml", root) == run("pack", root) == 0
    assert file_hash(root / "pack.zip") != file_hash(packed)
    assert run("verify", root / "pack.zip") == 0
    output = capsys.readouterr().out
    assert "PRESERVATION PASS" in output and "RECOMPUTATION PASS" in output
    assert "NOTE This does not show the package was not replaced wholesale" in output


def test_package_grids_only(packed, tmp_path):
    isolated = tmp_path / "proj"
    isolated.mkdir()
    shutil.copyfile(Path(PROJ_DATA) / "proj.db", isolated / "proj.db")
    # A fresh process has a database directory with no grids. Point package lookup
    # at an absent directory to ensure verification uses its extracted grids.
    code = """
import sys
import pyproj
import scenepack.verify as verifier
pyproj.datadir.set_data_dir(sys.argv[2])
verifier.PROJ_DATA = sys.argv[2]
sys.exit(verifier.verify_package(sys.argv[1]))
"""
    # close_fds=False makes CPython use posix_spawn. On macOS a fork()+exec child segfaults (rc -11) once PROJ
    # has run a grid-availability check in this process (reproduced 2026-10-11 after test_no_vertical_grids).
    result = subprocess.run([sys.executable, "-c", code, str(packed), str(isolated)], capture_output=True, text=True,
                            close_fds=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RECOMPUTATION PASS" in result.stdout and "BYTE-IDENTICAL yes" in result.stdout


def test_exports(packed):
    root = packed.parent
    tree = ET.parse(root / "exports/scene.kml")
    ns = {"k": NS}
    document = tree.find("k:Document", ns)
    assert document.find("k:name", ns).text == "scenepack — Synthetic SLC test — illustrative aid"
    folders = document.findall("k:Folder", ns)
    assert len(folders) == 3
    assert folders[0].find("k:Placemark/k:description", ns).text == DISCLAIMER + " Synthetic scene."
    points = folders[1].findall("k:Placemark", ns)
    assert len(points) == 40
    assert sum(p.find("k:Point/k:altitudeMode", ns).text == "clampToGround" for p in points) == 2
    assert all("class: synthetic" in p.find("k:description", ns).text for p in points)
    geojson = read_json(root / "exports/scene.geojson")
    assert len(geojson["features"]) == 40
    assert sum(len(f["geometry"]["coordinates"]) == 2 for f in geojson["features"]) == 2


def test_dataclass_roundtrips(packed):
    for model, value in (
        (Scene, Scene().to_json()), (Limits, Limits().to_json()),
        (Point, json.dumps(read_json(packed.parent / "points.json")["points"][0])),
        (Receipt, (packed.parent / "transform/receipt.json").read_text()),
        (Manifest, (packed.parent / "MANIFEST.json").read_text()),
    ):
        obj = model.from_json(value)
        assert asdict(model.from_json(obj.to_json())) == asdict(obj)


def test_csv_bytes_and_description(tmp_path):
    csv = tmp_path / "quoted.csv"
    data = b'CP1,7428000,1551000,4260,"  CURB, <&>  "\r\n'
    csv.write_bytes(data)
    root = make_scene(tmp_path / "quoted", csv_path=csv)
    assert (root / "raw/quoted.csv").read_bytes() == data
    p = read_json(root / "points.json")["points"][0]
    assert p["desc"] == "  CURB, <&>  "
    assert p["source"]["row"] == 1
    assert p["uncertainty"] == "not_established"
    assert run("transform", root) == run("export-kml", root) == 0
    tree = ET.parse(root / "exports/scene.kml")
    descriptions = tree.findall(f".//{{{NS}}}description")
    assert "  CURB, <&>  " in descriptions[1].text


@pytest.mark.parametrize("field,cause", [("pyproj_version", "pyproj"), ("proj_version", "PROJ"),
                                       ("proj_db_sha256", "proj.db")])
def test_runtime_differs(packed, capsys, field, cause):
    root = packed.parent
    receipt = read_json(root / "transform/receipt.json")
    receipt[field] = "different-runtime"
    receipt["receipt_id"] = receipt_identity(receipt)
    derived = read_json(root / "transform/derived.json")
    derived["receipt_id"] = receipt["receipt_id"]
    write_json(root / "transform/derived.json", derived)
    receipt["output_sha256"] = file_hash(root / "transform/derived.json")
    write_json(root / "transform/receipt.json", receipt)
    assert run("pack", root) == 0
    assert run("verify", packed) == 0
    output = capsys.readouterr().out
    assert "RECOMPUTATION PASS" in output
    assert f"BYTE-IDENTICAL not asserted (runtime differs: {cause}" in output
    assert "BYTE-IDENTICAL yes" not in output


def test_pack_records_inconsistent_transform(packed, capsys):
    root = packed.parent
    derived = read_json(root / "transform/derived.json")
    derived["derived"][0]["lon"] += 0.01
    write_json(root / "transform/derived.json", derived)
    assert run("pack", root) == 0
    assert read_json(root / "MANIFEST.json")["limits"]["internal_consistency"] == "not_satisfied"
    with zipfile.ZipFile(packed) as archive:
        cover = archive.read("COVER.md").decode()
    assert "Proves: Nothing beyond file hashes: internal_consistency = not_satisfied." in cover
    assert "converted by the recorded recipe" not in cover
    assert run("verify", packed) == 2
    output = capsys.readouterr().out
    assert "PRESERVATION PASS" in output and "RECOMPUTATION FAIL" in output


def test_verify_errors_use_stderr(packed, capsys):
    from test_fault_injection import rewrite
    rewrite(packed, "raw/slc-synthetic-ftus.csv", lambda b: b + b"\n")
    capsys.readouterr()
    assert run("verify", packed) == 2
    output = capsys.readouterr()
    assert "PRESERVATION FAIL" in output.out and "RECOMPUTATION FAIL" in output.out
    assert "ERROR:" in output.err and "ERROR:" not in output.out
    assert "HASH MISMATCH raw/slc-synthetic-ftus.csv" in output.err
    assert output.out.splitlines()[-1].startswith("NOTE ")


@pytest.mark.parametrize("failure", ["missing", "unsafe", "hash"])
def test_verify_note_is_last_terminal_line(packed, tmp_path, failure):
    if failure == "missing":
        packed = tmp_path / "missing.zip"
    elif failure == "unsafe":
        with zipfile.ZipFile(packed, "a") as archive:
            archive.writestr("../escape", "unsafe")
    else:
        from test_fault_injection import rewrite
        rewrite(packed, "exports/scene.kml", lambda b: b + b"edited")
    result = subprocess.run([sys.executable, "-u", "-m", "scenepack.cli", "verify", str(packed)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, close_fds=False)
    assert result.returncode == 2, result.stdout
    assert "ERROR:" in result.stdout or "REFUSED:" in result.stdout
    assert result.stdout.splitlines()[-1].startswith("NOTE ")
