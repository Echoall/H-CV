import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def roi_detector_calls(path, function):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    entrypoint = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == function)
    return [
        (node.func.attr, len(node.args), {keyword.arg for keyword in node.keywords})
        for node in ast.walk(entrypoint)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "detector"
        and any(keyword.arg == "roi_pixels" for keyword in node.keywords)
    ]


def test_board_and_matrix_use_the_same_roi_detector_entrypoint():
    production = roi_detector_calls("k230/main_hcv.py", "run")
    evaluation = roi_detector_calls("tools/eval/m1_equivalence.py", "_run_case")
    assert production == evaluation == [("update", 1, {"predicted_u", "roi_pixels"})]
