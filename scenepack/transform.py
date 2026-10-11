"""Explicit offline CRS operations and their reproducible receipts."""

from dataclasses import asdict
import math
from pathlib import Path
import shutil
import warnings

import pyproj
from pyproj.transformer import TransformerGroup

from . import GRIDS, PROJ_DATA, __version__
from .model import Receipt, Refused, canonical, file_hash, load_scene, read_json, sha256, write_json

GRID_NAMES = ("us_noaa_g2018u0.tif", "us_nga_egm96_15.tif")
FRAME_NOTE = "NAD83(2011)→WGS84 treated as identity by PROJ; ~1–2 m real offset in CONUS"


def transformer_grids(transformer, trusted_dir):
    trusted_dir = Path(trusted_dir).resolve()
    operations = transformer.operations
    grids = (grid for op in operations for grid in op.grids) if operations is not None else transformer.grids
    records = []
    for grid in grids:
        if grid.available:
            path = Path(grid.full_name).resolve()
            if not path.is_relative_to(trusted_dir):
                raise Refused(f"grid resolved outside the trusted directory: {path}")
            records.append(dict(name=grid.short_name, resolved_path=path.relative_to(trusted_dir).as_posix(),
                                sha256=file_hash(path)))
    return records


def build_transformer(src, dst="EPSG:9707", *, trusted_dir=GRIDS):
    pyproj.network.set_network_enabled(False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        group = TransformerGroup(src, dst, always_xy=True, allow_ballpark=False)
    if not group.transformers:
        unavailable = "; ".join(
            f"{op.name}: missing grids " + ", ".join(g.short_name for g in op.grids if not g.available)
            for op in group.unavailable_operations
        )
        raise Refused(f"no available non-ballpark operation: {unavailable}")
    chosen = group.transformers[0]
    pipeline = chosen.to_proj4() or ""
    missing = [name for name in GRID_NAMES if name not in pipeline]
    if ("noop" in pipeline or "ballpark" in pipeline.lower() or missing
            or chosen.accuracy is None or chosen.accuracy < 0
            or "ballpark" in chosen.description.lower()
            or any(getattr(op, "has_ballpark_transformation", False) for op in (chosen.operations or ()))):
        raise Refused(f"unacceptable operation {chosen.description}; missing grids {', '.join(missing)}; pipeline {pipeline}")
    transformer_grids(chosen, trusted_dir)
    return chosen


def receipt_identity(receipt):
    # Excluding output_sha256 breaks the receipt/derived-file hash cycle.
    body = {k: v for k, v in receipt.items() if k not in ("receipt_id", "output_sha256")}
    return sha256(canonical(body).encode("utf-8"))


def derive(points, transformer, h_crs, receipt_id):
    horizontal = None
    rows = []
    for point in points:
        if point["z"] is None:
            if horizontal is None:
                horizontal = pyproj.Transformer.from_crs(h_crs, "EPSG:4326", always_xy=True, allow_ballpark=False)
            lon, lat = horizontal.transform(point["e"], point["n"], errcheck=True)
            height = None
        else:
            lon, lat, height = transformer.transform(point["e"], point["n"], point["z"], errcheck=True)
        if not all(math.isfinite(v) for v in (lon, lat)) or (height is not None and not math.isfinite(height)):
            raise Refused(f"{point['id']}: non-finite transformed coordinate")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise Refused(f"{point['id']}: transformed coordinate outside Earth")
        row = dict(id=point["id"], derived_from=point["id"], lon=lon, lat=lat,
                   h_egm96_m=height, **{"class": "derived"},
                   recipe=horizontal.to_proj4() if height is None else transformer.to_proj4())
        if height is None:
            row["height"] = "none"
        rows.append(row)
    return {"derived": rows, "receipt_id": receipt_id}


def transform_scene(root):
    root = Path(root)
    scene = load_scene(root)
    if scene.control_tie["mode"] != "tied":
        raise Refused("local-only scene has no place on the Earth; pack and verify still work")
    source = read_json(root / "points.json")
    for key in ("h_crs", "v_crs", "h_unit", "v_unit"):
        if getattr(scene, key) != source["import"][key]:
            raise Refused(f"scene.toml and import disagree: {key}")
    src = f"{scene.h_crs}+{scene.v_crs}"
    transformer = build_transformer(src)
    receipt = asdict(Receipt(
        receipt_id="", kind="crs_transform", code_sha256=file_hash(__file__),
        scenepack_version=__version__, pyproj_version=pyproj.__version__,
        proj_version=pyproj.proj_version_str, proj_db_sha256=file_hash(Path(PROJ_DATA) / "proj.db"),
        src_crs=src, dst_crs="EPSG:9707", operation_name=transformer.description,
        pipeline=transformer.to_proj4(), accuracy_m=transformer.accuracy,
        grids=transformer_grids(transformer, GRIDS),
        frame_note=FRAME_NOTE, quantization="repr(float)", input_sha256=file_hash(root / "points.json"),
        output_sha256="",
    ))
    receipt["receipt_id"] = receipt_identity(receipt)
    derived = derive(source["points"], transformer, scene.h_crs, receipt["receipt_id"])
    receipt["output_sha256"] = sha256(canonical(derived).encode("utf-8"))
    target = root / "transform"
    (target / "grids").mkdir(parents=True, exist_ok=True)
    for grid in receipt["grids"]:
        shutil.copyfile(GRIDS / grid["resolved_path"], target / "grids" / grid["name"])
    write_json(target / "derived.json", derived)
    write_json(target / "receipt.json", receipt)
    return len(derived["derived"])
