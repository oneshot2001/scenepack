import math

import pyproj

from scenepack.model import read_json
from conftest import FIXTURES, make_scene, run


def horizontal_error(actual, expected):
    return (abs(actual["lon"] - float(expected["lon"])) * 111_320 * math.cos(math.radians(actual["lat"])),
            abs(actual["lat"] - float(expected["lat"])) * 111_320)


def assert_known_horizontal(derived):
    known = read_json(FIXTURES / "known-answers.json")
    for actual, expected in zip(derived["derived"][:3], known["points"]):
        assert max(horizontal_error(actual, expected["ncat"])) <= known["tolerances"]["horizontal_m"]


def test_known_answers(scene):
    assert run("transform", scene) == 0
    derived = read_json(scene / "transform/derived.json")
    known = read_json(FIXTURES / "known-answers.json")
    assert_known_horizontal(derived)
    ellipsoid = pyproj.Transformer.from_crs("EPSG:6625+6360", "EPSG:6319", always_xy=True, allow_ballpark=False)
    for actual, expected in zip(derived["derived"][:3], known["points"]):
        source = expected["input"]
        _, _, h = ellipsoid.transform(source["e_ftUS"], source["n_ftUS"], source["H_navd88_ftUS"], errcheck=True)
        independent_h = expected["derived"]["H_navd88_m"] + expected["ngs_geoid18"]["N_m"]
        assert abs(independent_h - expected["derived"]["h_ellipsoid_nad83_2011_m"]) < 1e-10
        assert abs(h - independent_h) <= known["tolerances"]["geoid18_step_m"]
        assert abs(actual["h_egm96_m"] - expected["derived"]["H_egm96_m_expected"]) <= known["tolerances"]["egm96_step_m"]
    receipt = read_json(scene / "transform/receipt.json")
    assert "us_noaa_g2018u0.tif" in receipt["pipeline"]
    assert "us_nga_egm96_15.tif" in receipt["pipeline"]
    assert "ballpark" not in receipt["pipeline"].lower()
    assert "noop" not in receipt["pipeline"]
    assert len(derived["derived"]) == 40
    assert sum(p.get("height") == "none" for p in derived["derived"]) == 2


def test_metres_match_feet(scene, tmp_path):
    metres = make_scene(tmp_path / "metres", metres=True)
    assert run("transform", scene) == run("transform", metres) == 0
    feet_data = read_json(scene / "transform/derived.json")["derived"]
    metre_data = read_json(metres / "transform/derived.json")["derived"]
    for feet, metre in zip(feet_data, metre_data):
        assert max(horizontal_error(feet, metre)) <= 0.001
        if feet["h_egm96_m"] is not None:
            assert abs(feet["h_egm96_m"] - metre["h_egm96_m"]) <= 0.001
        else:
            assert metre["h_egm96_m"] is None
