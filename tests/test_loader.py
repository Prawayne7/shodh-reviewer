"""Loader tests: missing, invalid and oversized artifacts stay visible; nothing is executed."""
import json
import shutil
from pathlib import Path

from reviewer.loader import MAX_BUNDLE_BYTES, load_bundle

EXAMPLE = Path(__file__).parent / "data" / "example_bundle"


def copy_bundle(tmp_path):
    dst = tmp_path / "b99"
    shutil.copytree(EXAMPLE, dst)
    return dst


def issue_codes(bundle):
    return [i.code for i in bundle.load_issues]


def test_missing_artifact_is_reported_and_others_still_load(tmp_path):
    root = copy_bundle(tmp_path)
    (root / "workflow.json").unlink()
    b = load_bundle(root)
    assert issue_codes(b) == ["U-MISSING-ARTIFACT"]
    assert b.workflow is None and b.spec is not None and b.requirements is not None


def test_invalid_yaml_and_invalid_schema_are_reported(tmp_path):
    root = copy_bundle(tmp_path)
    (root / "spec.yaml").write_text("rules: [unclosed", encoding="utf-8")
    (root / "workflow.json").write_text(json.dumps({"version": "wf", "start": "n1"}), encoding="utf-8")
    b = load_bundle(root)
    assert issue_codes(b) == ["U-SCHEMA", "U-SCHEMA"]
    assert b.spec is None and b.workflow is None


def test_unknown_key_in_an_artifact_is_a_schema_issue(tmp_path):
    root = copy_bundle(tmp_path)
    text = (root / "requirements.yaml").read_text(encoding="utf-8")
    (root / "requirements.yaml").write_text(text + "expected_status: PASS\n", encoding="utf-8")
    b = load_bundle(root)
    assert issue_codes(b) == ["U-SCHEMA"] and b.requirements is None


def test_oversized_bundle_is_not_truncated_or_reviewed(tmp_path):
    root = copy_bundle(tmp_path)
    (root / "code" / "big.py").write_text("x = 1\n" * (MAX_BUNDLE_BYTES // 6 + 10), encoding="utf-8")
    b = load_bundle(root)
    assert issue_codes(b) == ["U-SIZE-LIMIT"]
    assert b.spec is None and b.code == []


def test_code_path_traversal_is_rejected(tmp_path):
    root = copy_bundle(tmp_path)
    (tmp_path / "secret.py").write_text("def route(t): return 'x'\n", encoding="utf-8")
    idx = json.loads((root / "code" / "index.json").read_text(encoding="utf-8"))
    idx["snippets"][0]["file"] = "../../secret.py"
    (root / "code" / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    b = load_bundle(root)
    assert issue_codes(b) == ["U-SCHEMA"] and b.code == []


def test_approvals_accept_bare_array_or_wrapped_object(tmp_path):
    root = copy_bundle(tmp_path)
    records = json.loads((root / "approvals.json").read_text(encoding="utf-8"))
    (root / "approvals.json").write_text(json.dumps({"version": "appr-v4", "records": records}), encoding="utf-8")
    b = load_bundle(root)
    assert b.approvals.records[0].evidence.cite() == "approvals@appr-v4#A1"
