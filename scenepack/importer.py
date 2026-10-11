"""Byte-preserving CSV import with explicit axes, units, and provenance."""

import csv
import io
import json
import math
from pathlib import Path
import re

from pyproj import CRS

from .model import Point, Refused, load_scene, save_scene, sha256, write_json

NUMERIC = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def check_units(crs_code, unit, vertical=False):
    if not re.fullmatch(r"EPSG:[0-9]+", crs_code):
        raise Refused(f"CRS must be EPSG:n: {crs_code}")
    crs = CRS(crs_code)
    if vertical and (not crs.is_vertical or crs.is_compound or len(crs.axis_info) != 1):
        raise Refused(f"vertical CRS required: {crs_code}")
    if not vertical and (not crs.is_projected or crs.is_compound or len(crs.axis_info) != 2):
        raise Refused(f"projected horizontal CRS required: {crs_code}")
    declared = crs.axis_info[0].unit_name
    if {"US survey foot": "ftUS", "metre": "m"}.get(declared) != unit:
        raise Refused(f"{crs_code} declares {declared}, contradicting {unit}")


def number(value, row, column):
    if not NUMERIC.fullmatch(value):
        raise Refused(f"row {row}: non-numeric {column}: {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise Refused(f"row {row}: NaN/inf in {column}")
    return result


def parse_csv(data, settings, point_class):
    if settings["order"] not in ("PNEZD", "PENZD"):
        raise Refused("unknown CSV order")
    if point_class not in ("measured", "synthetic"):
        raise Refused("unknown point class")
    check_units(settings["h_crs"], settings["h_unit"])
    check_units(settings["v_crs"], settings["v_unit"], vertical=True)
    if settings["h_unit"] != settings["v_unit"]:
        raise Refused("mixed horizontal and vertical units are not supported")
    source_hash = sha256(data)
    points = []
    reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
    for row_index, row in enumerate(reader, 1):
        if not row or (len(row) == 1 and not row[0].strip()):
            raise Refused(f"row {row_index}: blank line")
        if len(row) != 5:
            raise Refused(f"row {row_index}: expected five CSV columns")
        fields = dict(zip(settings["order"], row))
        if row_index == 1 and all(not NUMERIC.fullmatch(value) for value in row):
            continue
        n = number(fields["N"], row_index, "N")
        e = number(fields["E"], row_index, "E")
        z = number(fields["Z"], row_index, "Z") if fields["Z"] else None
        point = Point(f"p{len(points) + 1:04d}", fields["P"], n, e, z,
                      fields["D"], point_class, {"file_sha256": source_hash, "row": row_index})
        points.append(json.loads(point.to_json()))
    if not points:
        raise Refused("CSV contains no points")
    return points


def import_csv(root, csv_path, *, order, h_unit, v_unit, h_crs, v_crs, point_class=None):
    root, csv_path = Path(root), Path(csv_path)
    if (root / "points.json").exists():
        raise Refused("second import is not supported; points.json already exists")
    scene = load_scene(root)
    point_class = point_class or scene.point_class_default
    settings = dict(file=csv_path.name, order=order, h_unit=h_unit, v_unit=v_unit,
                    h_crs=h_crs, v_crs=v_crs)
    data = csv_path.read_bytes()
    points = parse_csv(data, settings, point_class)
    settings.update(file_sha256=sha256(data), row_count=len(points))
    (root / "raw" / csv_path.name).write_bytes(data)
    write_json(root / "points.json", {"points": points, "import": settings})
    for key in ("h_unit", "v_unit", "h_crs", "v_crs"):
        setattr(scene, key, settings[key])
    scene.point_class_default = point_class
    save_scene(root, scene)
    return len(points)
