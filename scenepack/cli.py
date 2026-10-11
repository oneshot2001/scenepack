"""The six intentionally small command-line operations."""

import argparse
import csv
from pathlib import Path
import sys
import zipfile

import pyproj

from .importer import import_csv
from .kml import export_kml
from .model import Refused, Scene, save_scene
from .pack import pack_scene
from .transform import transform_scene
from .verify import verify_package


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, f"ERROR: {message}\n")


def init_scene(root):
    root = Path(root)
    if (root / "scene.toml").exists():
        raise Refused("scene.toml already exists")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("raw", "exports", "transform"):
        (root / name).mkdir(exist_ok=True)
    save_scene(root, Scene())


def main(argv=None):
    parser = Parser(prog="scenepack")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "transform", "export-kml", "pack"):
        command = sub.add_parser(name)
        command.add_argument("dir", type=Path)
    command = sub.add_parser("import")
    command.add_argument("dir", type=Path)
    command.add_argument("csv", type=Path)
    command.add_argument("--order", required=True, choices=("PNEZD", "PENZD"))
    for name in ("h-unit", "v-unit"):
        command.add_argument(f"--{name}", required=True, choices=("ftUS", "m"))
    for name in ("h-crs", "v-crs"):
        command.add_argument(f"--{name}", required=True)
    command.add_argument("--class", dest="point_class", choices=("measured", "synthetic"))
    command = sub.add_parser("verify")
    command.add_argument("zip", type=Path)
    command.add_argument("--tolerance-m", type=float, default=0.001)
    command.add_argument("--work", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            init_scene(args.dir)
            print(f"INITIALIZED {args.dir}")
        elif args.command == "import":
            count = import_csv(args.dir, args.csv, **{k: getattr(args, k) for k in (
                "order", "h_unit", "v_unit", "h_crs", "v_crs", "point_class")})
            print(f"IMPORTED {count} points")
        elif args.command == "transform":
            print(f"TRANSFORMED {transform_scene(args.dir)} points")
        elif args.command == "export-kml":
            print(f"EXPORTED {export_kml(args.dir)} points to exports/scene.kml and exports/scene.geojson")
        elif args.command == "pack":
            pack_scene(args.dir)
        elif args.command == "verify":
            return verify_package(args.zip, args.tolerance_m, args.work)
        return 0
    except (Refused, OSError, ValueError, KeyError, TypeError, csv.Error,
            pyproj.exceptions.ProjError, zipfile.BadZipFile, RuntimeError) as exc:
        prefix = "REFUSED" if isinstance(exc, Refused) else "ERROR"
        print(f"{prefix}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
