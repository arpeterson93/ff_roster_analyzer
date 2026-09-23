import json
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "trade_case.json"
TRADE_JS_PATH = Path(__file__).parent.parent / "docs" / "js" / "trade.js"

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node not on PATH - CI image has node, this is a local-only skip")
def test_trade_js_matches_python_engine():
    result = subprocess.run(
        [NODE, str(TRADE_JS_PATH), "--fixture", str(FIXTURE_PATH)],
        capture_output=True, text=True, check=True,
    )
    output = json.loads(result.stdout)
    js_results = {r["name"]: r for r in output["cases"]}
    js_streaming_results = {r["name"]: r for r in output["streaming_cases"]}
    js_slot_value_matrix_results = {r["name"]: r for r in output["slot_value_matrix_cases"]}

    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)

    for case in fixture["cases"]:
        js = js_results[case["name"]]
        expected = case["expected"]
        assert js["gain_a"] == pytest.approx(expected["gain_a"]), case["name"]
        assert js["gain_b"] == pytest.approx(expected["gain_b"]), case["name"]
        assert js["favors"] == expected["favors"], case["name"]

    for case in fixture.get("streaming_cases", []):
        js = js_streaming_results[case["name"]]
        expected = case["expected"]
        assert js["gain_a"] == pytest.approx(expected["gain_a"]), case["name"]
        assert js["gain_b"] == pytest.approx(expected["gain_b"]), case["name"]
        assert js["favors"] == expected["favors"], case["name"]

    for case in fixture.get("slot_value_matrix_cases", []):
        js_matrix = js_slot_value_matrix_results[case["name"]]["matrix"]
        expected = case["expected"]
        assert set(js_matrix.keys()) == set(expected.keys()), case["name"]
        for label, exp_vals in expected.items():
            assert js_matrix[label]["startingValue"] == pytest.approx(exp_vals["starting_value"]), f"{case['name']}:{label}"
            assert js_matrix[label]["depthValue"] == pytest.approx(exp_vals["depth_value"]), f"{case['name']}:{label}"
            assert js_matrix[label]["total"] == pytest.approx(exp_vals["total"]), f"{case['name']}:{label}"
