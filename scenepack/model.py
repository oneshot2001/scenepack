"""Data records and the small on-disk JSON/TOML formats."""

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
import tomllib


class Refused(ValueError):
    pass


def canonical(obj):
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    return sha256(Path(path).read_bytes())


def read_json(path):
    def invalid(value):
        raise Refused(f"non-finite JSON number: {value}")

    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid)


def write_json(path, obj):
    Path(path).write_text(canonical(obj), encoding="utf-8")


class JsonRecord:
    def to_json(self):
        obj = asdict(self)
        if "class_" in obj:
            obj["class"] = obj.pop("class_")
        return canonical(obj)

    @classmethod
    def from_json(cls, value):
        obj = json.loads(value)
        if "class" in obj:
            obj["class_"] = obj.pop("class")
        return cls(**obj)


@dataclass
class Point(JsonRecord):
    id: str
    name: str
    n: float
    e: float
    z: float | None
    desc: str
    class_: str
    source: dict
    uncertainty: str | dict = "not_established"


@dataclass
class Scene(JsonRecord):
    agency: str = ""
    case_label: str = ""
    operator: str = ""
    instrument: dict = field(default_factory=lambda: {
        "model": "", "serial": "", "calibration_ref": "",
    })
    control_tie: dict = field(default_factory=lambda: {
        "mode": "local-only", "control_points": [], "method": "",
        "translation": [], "rotation_deg": 0.0, "scale": 1.0,
        "residuals_m": [], "supporting_record_hashes": [], "missing_records": [],
    })
    rounding_policy: str = "repr(float)"
    h_unit: str = ""
    v_unit: str = ""
    h_crs: str = ""
    v_crs: str = ""
    point_class_default: str = "measured"


@dataclass
class Limits(JsonRecord):
    internal_consistency: str = "not_evaluated"
    source_authenticity: str = "not_established"
    field_accuracy: str = "not_established"
    completeness: str = "not_established"
    custody_continuity: str = "not_established"
    legal_admissibility: str = "not_established"


@dataclass
class Receipt(JsonRecord):
    receipt_id: str
    kind: str
    code_sha256: str
    scenepack_version: str
    pyproj_version: str
    proj_version: str
    proj_db_sha256: str
    src_crs: str
    dst_crs: str
    operation_name: str
    pipeline: str
    accuracy_m: float
    grids: list
    frame_note: str
    quantization: str
    input_sha256: str
    output_sha256: str


@dataclass
class Manifest(JsonRecord):
    scenepack_version: str
    packed_at: str
    files: dict
    limits: dict
    tolerance_m: float
    runtime: dict


def load_scene(root):
    with (Path(root) / "scene.toml").open("rb") as f:
        obj = tomllib.load(f)
    scene = Scene(**obj)
    if scene.control_tie["mode"] not in ("tied", "local-only"):
        raise Refused("unknown control_tie.mode")
    if scene.point_class_default not in ("measured", "synthetic"):
        raise Refused("unknown point_class_default")
    return scene


def toml_value(value):
    if isinstance(value, str):
        # JSON's string escapes are also valid TOML basic-string escapes.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise Refused("non-finite scene value")
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(v)}" for k, v in value.items()) + " }"
    raise Refused(f"unsupported scene value: {type(value).__name__}")


def save_scene(root, scene):
    obj = asdict(scene)
    lines = [f"{k} = {toml_value(v)}" for k, v in obj.items() if not isinstance(v, dict)]
    for table, values in obj.items():
        if isinstance(values, dict):
            lines.extend(["", f"[{table}]"])
            lines.extend(f"{k} = {toml_value(v)}" for k, v in values.items())
    (Path(root) / "scene.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
