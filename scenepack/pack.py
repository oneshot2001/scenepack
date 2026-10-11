"""Sorted, fixed-timestamp archives and an explicit preservation manifest."""

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import platform
import zipfile

import pyproj

from . import __version__
from .model import Limits, Manifest, Refused, canonical, file_hash, load_scene, read_json, sha256, write_json


def cover_text(root, scene, source, receipt, manifest):
    settings = source["import"]
    values = dict(
        package_digest="See PACKAGE DIGEST printed by pack; the ZIP cannot contain its own SHA-256",
        packed_at=manifest["packed_at"], scenepack_version=__version__,
        pyproj_version=pyproj.__version__, proj_version=pyproj.proj_version_str,
        agency=scene.agency, case_label=scene.case_label, operator=scene.operator,
        instrument_model=scene.instrument["model"], instrument_serial=scene.instrument["serial"],
        calibration_ref=scene.instrument["calibration_ref"],
        control_tie_summary=canonical(scene.control_tie).strip(),
        missing_records=", ".join(scene.control_tie["missing_records"]) or "None declared",
        raw_file=settings["file"], raw_sha256=settings["file_sha256"], point_count=len(source["points"]),
        column_order=settings["order"], h_unit=scene.h_unit, v_unit=scene.v_unit,
        h_crs=scene.h_crs, v_crs=scene.v_crs,
        class_counts=", ".join(f"{k}: {v}" for k, v in sorted(Counter(p["class"] for p in source["points"]).items())),
        uncertainty_status="not_established",
        proves_summary=("the numbers in this package are the numbers that were imported, converted by the recorded recipe; "
                        "every file agrees with the included manifest (subject to the independent digest comparison above)."
                        if manifest["limits"]["internal_consistency"] == "satisfied" else
                        f"Nothing beyond file hashes: internal_consistency = {manifest['limits']['internal_consistency']}."),
        transform_summary=(f"Receipt {receipt['receipt_id']}\n\n{receipt['operation_name']}\n\n"
                           f"{receipt['frame_note']}\n\nInternal consistency: {manifest['limits']['internal_consistency']}"
                           if receipt else "No transform; internal consistency: not_evaluated"),
        exports_summary=", ".join(sorted(p.name for p in (root / "exports").glob("*") if p.is_file())) or "None",
        tolerance_m=manifest["tolerance_m"], **manifest["limits"],
    )
    text = Path(__file__).with_name("COVER.md.tmpl").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def pack_scene(root):
    root = Path(root)
    scene = load_scene(root)
    source = read_json(root / "points.json")
    paths = [root / "scene.toml", root / "points.json"]
    for directory in ("raw", "transform", "exports"):
        base = root / directory
        if base.is_symlink():
            raise Refused(f"symlink package input: {directory}")
        for path in base.rglob("*"):
            if path.is_symlink():
                raise Refused(f"symlink package input: {path.relative_to(root)}")
            if path.is_file():
                paths.append(path)
    if any(p.is_symlink() for p in paths):
        raise Refused("symlink package input")
    entries = {p.relative_to(root).as_posix(): p.read_bytes() for p in paths}
    files = {name: sha256(data) for name, data in entries.items()}
    limits = Limits()
    receipt = None
    if "transform/receipt.json" in entries or "transform/derived.json" in entries:
        limits.internal_consistency = "not_satisfied"
        if "transform/receipt.json" in entries:
            receipt = read_json(root / "transform/receipt.json")
            if (files.get("transform/derived.json") == receipt["output_sha256"]
                    and files["points.json"] == receipt["input_sha256"]):
                limits.internal_consistency = "satisfied"
    runtime = dict(python=platform.python_version(), pyproj=pyproj.__version__, proj=pyproj.proj_version_str)
    packed_at = datetime.now(timezone.utc).isoformat()
    # Keep the original packing time when the complete input snapshot is unchanged.
    previous_path = root / "MANIFEST.json"
    if previous_path.exists():
        previous = read_json(previous_path)
        old_files = {k: v for k, v in previous["files"].items() if k != "COVER.md"}
        if (old_files == files and previous["runtime"] == runtime
                and previous["scenepack_version"] == __version__ and previous["limits"] == asdict(limits)):
            packed_at = previous["packed_at"]
    manifest = asdict(Manifest(__version__, packed_at, files, asdict(limits), 0.001, runtime))
    cover = cover_text(root, scene, source, receipt, manifest)
    entries["COVER.md"] = cover.encode("utf-8")
    manifest["files"]["COVER.md"] = sha256(entries["COVER.md"])
    entries["MANIFEST.json"] = canonical(manifest).encode("utf-8")
    (root / "COVER.md").write_text(cover, encoding="utf-8")
    write_json(previous_path, manifest)
    with zipfile.ZipFile(root / "pack.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    digest = file_hash(root / "pack.zip")
    print(f"PACKAGE DIGEST {digest}")
    print("Record this somewhere the package cannot touch (evidence log, email to yourself, notebook).")
    return digest
