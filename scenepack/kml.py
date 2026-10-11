"""Illustrative KML and GeoJSON, with source units and limitations visible."""

from pathlib import Path
import xml.etree.ElementTree as ET

from .model import Refused, file_hash, load_scene, read_json, sha256, write_json

NS = "http://www.opengis.net/kml/2.2"
DISCLAIMER = ("Intended as an illustrative aid (FRE 107); its legal status is for the court. "
              "Measurements are in the package, not in this file. "
              "Background imagery is Google's and was not used for any measurement.")


def element(parent, name, text=None):
    node = ET.SubElement(parent, f"{{{NS}}}{name}")
    node.text = text
    return node


def placemark(parent, name, description, lon, lat, height=None):
    node = element(parent, "Placemark")
    element(node, "name", name)
    if description is not None:
        element(node, "description", description)
    point = element(node, "Point")
    element(point, "altitudeMode", "clampToGround" if height is None else "absolute")
    element(point, "coordinates", f"{lon!r},{lat!r},{0 if height is None else repr(height)}")


def export_kml(root):
    root = Path(root)
    scene = load_scene(root)
    if scene.control_tie["mode"] != "tied":
        raise Refused("local-only scene has no place on the Earth; pack and verify still work")
    source = read_json(root / "points.json")
    derived = read_json(root / "transform/derived.json")
    receipt = read_json(root / "transform/receipt.json")
    if file_hash(root / "points.json") != receipt["input_sha256"] or file_hash(root / "transform/derived.json") != receipt["output_sha256"]:
        raise Refused("transform is stale; re-run transform before exporting")
    digest = sha256((derived["receipt_id"] + file_hash(root / "points.json")).encode())[:8]
    rows = derived["derived"]
    if not rows:
        raise Refused("no derived points to export")
    lon = sum(p["lon"] for p in rows) / len(rows)
    lat = sum(p["lat"] for p in rows) / len(rows)
    ET.register_namespace("", NS)
    tree = ET.Element(f"{{{NS}}}kml")
    document = element(tree, "Document")
    element(document, "name", f"scenepack — {scene.case_label} — illustrative aid")
    first = element(document, "Folder")
    element(first, "name", f"ILLUSTRATIVE AID — see package {digest}")
    disclaimer = DISCLAIMER + (" Synthetic scene." if scene.point_class_default == "synthetic" else "")
    placemark(first, "READ FIRST", disclaimer, lon, lat)
    folder = element(document, "Folder")
    crs = f"{scene.h_crs}+{scene.v_crs}"
    element(folder, "name", f"Points ({scene.h_unit}, {crs})")
    sources = {p["id"]: p for p in source["points"]}
    features = []
    for row in rows:
        p = sources[row["derived_from"]]
        unit = scene.h_unit if scene.h_unit == scene.v_unit else f"{scene.h_unit} (N/E); {scene.v_unit} (Z)"
        description = (f"{p['name']} · {p['desc']} · N {p['n']} E {p['e']} Z {p['z']} {unit} · {crs} · "
                       f"uncertainty: not_established · class: {p['class']} · package {digest}")
        placemark(folder, row["id"], description, row["lon"], row["lat"], row["h_egm96_m"])
        coordinates = [row["lon"], row["lat"]]
        if row["h_egm96_m"] is not None:
            coordinates.append(row["h_egm96_m"])
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": coordinates},
                         "properties": {**p, "description": description, "package": digest,
                                        "h_unit": scene.h_unit, "v_unit": scene.v_unit,
                                        "h_crs": scene.h_crs, "v_crs": scene.v_crs,
                                        "altitudeMode": "clampToGround" if row["h_egm96_m"] is None else "absolute"}})
    legend = element(document, "Folder")
    element(legend, "name", "Legend")
    placemark(legend, f"Legend — units {scene.h_unit}; north = geographic north; one unit system per output", None, lon, lat)
    (root / "exports").mkdir(exist_ok=True)
    ET.ElementTree(tree).write(root / "exports/scene.kml", encoding="utf-8", xml_declaration=True)
    write_json(root / "exports/scene.geojson", {"type": "FeatureCollection", "features": features})
    return len(rows)
