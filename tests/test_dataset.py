"""Dataset integrity: composition, determinism, label verification, no leakage."""
import json
from pathlib import Path

import yaml

from fixtures import check_generated
from oracle import check_labels, freeze
from reviewer.loader import load_bundle

ROOT = Path(__file__).resolve().parents[1]
LABELS = {p.stem: yaml.safe_load(p.read_text()) for p in sorted((ROOT / "labels").glob("b*.yaml"))}


def _count(**kw):
    return sum(all(l[k] == v for k, v in kw.items()) for l in LABELS.values())


def test_composition_meets_assessment_minimums():
    assert len(LABELS) >= 12
    assert _count(case_type="valid") >= 3
    assert _count(case_type="defective") >= 6
    assert _count(case_type="incomplete") >= 3
    assert _count(split="heldout") >= 4
    kinds = {l["split"] for l in LABELS.values()}
    assert kinds == {"dev", "heldout"}


def test_required_coverage_tags_present():
    tags = {t for l in LABELS.values() for t in l["tags"]}
    needed = {"approved_change", "unapproved_change", "approval_conflict", "missing_requirement", "boundary", "units",
              "precedence", "wrong_branch", "code_disagreement", "injected_instruction", "model_failure",
              "unusual_formulation", "multi_rule_interaction", "rename_ids", "reorder_records", "paraphrase"}
    assert needed <= tags, needed - tags


def test_paired_variants_exist_for_both_kinds():
    kinds = {l["pair"]["kind"] for l in LABELS.values() if l["pair"]}
    assert kinds == {"invariant", "behavior_change"}


def test_heldout_spans_all_case_types():
    held = {l["case_type"] for l in LABELS.values() if l["split"] == "heldout"}
    assert held == {"valid", "defective", "incomplete"}


def test_bundle_ids_are_opaque():
    assert all(len(b) == 3 and b[0] == "b" and b[1:].isdigit() for b in LABELS)


def test_labels_are_verified_by_independent_oracle():
    assert check_labels.check() == []


def test_committed_dataset_is_reproducible_from_seed():
    check_generated.main()


def test_every_bundle_loads_with_no_loader_issues():
    for bid in LABELS:
        b = load_bundle(ROOT / "fixtures" / "bundles" / bid)
        assert b.load_issues == [], bid


def test_no_label_field_inside_any_bundle():
    for bid in LABELS:
        for f in (ROOT / "fixtures" / "bundles" / bid).rglob("*"):
            if f.is_file():
                assert "expected_status" not in f.read_text(), f


def test_model_scripts_match_labels():
    for bid, l in LABELS.items():
        if l["model_script"]:
            assert json.loads((ROOT / l["model_script"]).read_text())["behavior"]


def test_heldout_manifest_matches_current_files():
    assert freeze.main(["--verify"]) == 0


def test_reviewer_package_never_imports_oracle_or_labels():
    for p in (ROOT / "reviewer").glob("*.py"):
        text = p.read_text()
        assert "import oracle" not in text and "from oracle" not in text, p
        assert "labels/" not in text.replace("Expected labels have no field", ""), p
