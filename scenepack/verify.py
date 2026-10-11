"""Two independent verdicts: file preservation and raw-data recomputation."""

import csv
import math
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import zipfile

import pyproj

from . import GRIDS, PROJ_DATA, set_search_path
from .importer import parse_csv
from .model import Refused, canonical, file_hash, load_scene, read_json
from .transform import GRID_NAMES, build_transformer, derive, receipt_identity, transformer_grids

NOTE = "NOTE This does not show the package was not replaced wholesale; compare the digest with your independently recorded copy."


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name
            or ":" in name or "\x00" in name or str(path) != name.rstrip("/")):
        raise Refused(f"unsafe ZIP path: {name!r}")
    return path


def extract(archive_path, target):
    with zipfile.ZipFile(archive_path) as archive:
        seen = set()
        for info in archive.infolist():
            name = info.filename
            path = safe_name(name)
            normalized = str(path)
            if normalized in seen:
                raise Refused(f"duplicate ZIP entry: {name}")
            seen.add(normalized)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise Refused(f"symlink ZIP entry: {name}")
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise Refused(f"unsupported ZIP entry: {name}")
        # Check file/directory collisions before extracting anything.
        names = {str(PurePosixPath(i.filename)): i.is_dir() for i in archive.infolist()}
        for name in names:
            for parent in PurePosixPath(name).parents:
                if str(parent) in names and not names[str(parent)]:
                    raise Refused(f"ZIP file/directory collision: {parent}")
        archive.extractall(target)


def preservation(root):
    issues = []
    manifest = read_json(root / "MANIFEST.json")
    listed = manifest["files"]
    for name, expected in sorted(listed.items()):
        safe_name(name)
        path = root / name
        if not path.is_file():
            issues.append(f"MISSING {name}")
        elif file_hash(path) != expected:
            issues.append(f"HASH MISMATCH {name}")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    for name in sorted(actual - set(listed) - {"MANIFEST.json"}):
        issues.append(f"UNLISTED {name}")
    for name in ("scene.toml", "points.json", "COVER.md"):
        if name not in listed:
            issues.append(f"MISSING manifest entry {name}")
    return issues


def recomputation(root, tolerance):
    receipt_path = root / "transform/receipt.json"
    derived_path = root / "transform/derived.json"
    if not receipt_path.exists() and not derived_path.exists():
        # A deleted transform must not masquerade as an untransformed package.
        manifest = read_json(root / "MANIFEST.json")
        if any(name.startswith("transform/") for name in manifest["files"]):
            return ["MISSING transform/receipt.json and transform/derived.json"], [], False
        return [], [], True
    receipt = read_json(receipt_path)
    saved = read_json(derived_path)
    source = read_json(root / "points.json")
    scene = load_scene(root)
    issues, details = [], []
    if receipt["receipt_id"] != receipt_identity(receipt):
        issues.append("receipt_id mismatch")
    if saved["receipt_id"] != receipt["receipt_id"]:
        issues.append("derived.json receipt_id mismatch")
    if file_hash(root / "points.json") != receipt["input_sha256"]:
        issues.append("points.json receipt input hash mismatch")
    if file_hash(derived_path) != receipt["output_sha256"]:
        issues.append("derived.json receipt output hash mismatch")
    if scene.control_tie["mode"] != "tied":
        issues.append("local-only scene has a transform")
    settings = source["import"]
    for key in ("h_crs", "v_crs", "h_unit", "v_unit"):
        if getattr(scene, key) != settings[key]:
            issues.append(f"scene.toml and import disagree: {key}")
    if receipt["src_crs"] != f"{settings['h_crs']}+{settings['v_crs']}" or receipt["dst_crs"] != "EPSG:9707":
        raise Refused("receipt CRS differs from recorded import/destination")
    raw_name = settings["file"]
    if safe_name(raw_name).name != raw_name:
        raise Refused(f"invalid raw file basename: {raw_name}")
    raw_path = root / "raw" / raw_name
    if file_hash(raw_path) != settings["file_sha256"]:
        issues.append(f"raw/{raw_name} import hash mismatch")
    points = parse_csv(raw_path.read_bytes(), settings, scene.point_class_default)
    if len(points) != settings["row_count"]:
        issues.append("raw row_count differs")
    if points != source["points"]:
        issues.append("points.json differs from re-parsed raw data")
    grids = receipt["grids"]
    if sorted(g["name"] for g in grids) != sorted(GRID_NAMES):
        raise Refused("receipt must name exactly the two required grids")
    grid_issues = []
    for grid in grids:
        path = root / "transform/grids" / grid["name"]
        if not path.is_file():
            grid_issues.append(f"MISSING grid {grid['name']}")
        elif file_hash(path) != grid["sha256"]:
            grid_issues.append(f"receipt grid hash mismatch: {grid['name']}")
    if grid_issues:
        return issues + grid_issues, details, False
    try:
        # Restrict lookup to the runtime database and the already checked package grids.
        pkg_grids = root / "transform/grids"
        set_search_path(pkg_grids)
        transformer = build_transformer(receipt["src_crs"], receipt["dst_crs"], trusted_dir=pkg_grids)
        resolved = transformer_grids(transformer, pkg_grids)
        if sorted(resolved, key=lambda g: g["name"]) != sorted(grids, key=lambda g: g["name"]):
            return issues + ["resolved grid paths or hashes differ from receipt"], details, False
        if transformer.to_proj4() != receipt["pipeline"]:
            return issues + ["pipeline differs"], details, False
        regenerated = derive(points, transformer, settings["h_crs"], receipt["receipt_id"])
    finally:
        set_search_path(GRIDS)
    saved_rows = saved["derived"]
    by_id = {p["id"]: p for p in saved_rows}
    if len(by_id) != len(saved_rows):
        issues.append("duplicate derived point id")
    expected_ids = {p["id"] for p in regenerated["derived"]}
    for extra in sorted(set(by_id) - expected_ids):
        issues.append(f"unexpected point {extra}")
    for actual in regenerated["derived"]:
        point_id = actual["id"]
        if point_id not in by_id:
            issues.append(f"MISSING point {point_id}")
            continue
        expected = by_id[point_id]
        values = [expected["lon"], expected["lat"]]
        if expected["h_egm96_m"] is not None:
            values.append(expected["h_egm96_m"])
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            issues.append(f"{point_id}: non-finite or invalid derived coordinate")
            continue
        dx = abs(actual["lon"] - expected["lon"]) * 111_320 * math.cos(math.radians(actual["lat"]))
        dy = abs(actual["lat"] - expected["lat"]) * 111_320
        az, ez = actual["h_egm96_m"], expected["h_egm96_m"]
        dz = abs(az - ez) if az is not None and ez is not None else None
        details.append(f"{point_id} |Δlon|={dx!r} m |Δlat|={dy!r} m |Δh|={'none' if dz is None else repr(dz) + ' m'}")
        if dx > tolerance or dy > tolerance or (dz is not None and dz > tolerance) or ((az is None) != (ez is None)):
            issues.append(f"{point_id}: coordinate residual exceeds tolerance {tolerance!r} m (or height presence differs)")
        for key in ("id", "derived_from", "class", "recipe", "height"):
            if actual.get(key) != expected.get(key):
                issues.append(f"{point_id}: {key} differs")
    runtime_differences = []
    if pyproj.__version__ != receipt["pyproj_version"]:
        runtime_differences.append(f"pyproj {receipt['pyproj_version']} → {pyproj.__version__}")
    if pyproj.proj_version_str != receipt["proj_version"]:
        runtime_differences.append(f"PROJ {receipt['proj_version']} → {pyproj.proj_version_str}")
    if file_hash(Path(PROJ_DATA) / "proj.db") != receipt["proj_db_sha256"]:
        runtime_differences.append("proj.db")
    if not runtime_differences:
        identical = canonical(regenerated).encode("utf-8") == derived_path.read_bytes()
        details.append(f"BYTE-IDENTICAL {'yes' if identical else 'no'}")
        if not identical:
            issues.append("derived.json is not byte-identical on the recorded runtime")
    else:
        details.append(f"BYTE-IDENTICAL not asserted (runtime differs: {'; '.join(runtime_differences)})")
    return issues, details, False


def verify_package(path, tolerance=0.001, work=None):
    try:
        if not math.isfinite(tolerance) or tolerance < 0:
            raise Refused("tolerance must be finite and nonnegative")
        if work is not None:
            Path(work).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="scenepack-", dir=work) as temporary:
            root = Path(temporary)
            extract(path, root)
            try:
                preserve = preservation(root)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                preserve = [str(exc)]
            print(f"PRESERVATION {'FAIL' if preserve else 'PASS'}")
            for issue in preserve:
                print(f"ERROR: {issue}", file=sys.stderr)
            try:
                issues, details, skipped = recomputation(root, tolerance)
            except (ValueError, OSError, KeyError, TypeError, pyproj.exceptions.ProjError) as exc:
                issues, details, skipped = [str(exc)], [], False
            verdict = "FAIL" if issues else "SKIPPED(no transform)" if skipped else "PASS"
            print(f"\nRECOMPUTATION {verdict}")
            for line in details:
                print(line)
            for issue in issues:
                print(f"ERROR: {issue}", file=sys.stderr)
            return 2 if preserve or issues else 0
    except (Refused, OSError, ValueError, KeyError, TypeError, csv.Error,
            pyproj.exceptions.ProjError, zipfile.BadZipFile, RuntimeError) as exc:
        prefix = "REFUSED" if isinstance(exc, Refused) else "ERROR"
        print(f"{prefix}: {exc}", file=sys.stderr)
        return 2
    finally:
        print(NOTE)
