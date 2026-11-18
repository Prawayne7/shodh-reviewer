from pathlib import Path

import pytest

from reviewer.loader import load_bundle
from reviewer.semantics import review_bundle

ROOT = Path(__file__).resolve().parents[1]


def findings(case):
    return review_bundle(load_bundle(ROOT / "fixtures" / "bundles" / case)).findings


def test_unit_mismatch_localizes_rule_and_keeps_fallback_trace():
    finding, = findings("b14")
    assert finding.location == "S3"
    assert finding.rule_ref == "R3"
    assert finding.counterexample.actual_by.location == "default"
    assert finding.counterexample.ticket.wait_minutes == 30


@pytest.mark.parametrize("artifact", ["spec", "workflow", "code:route.py"])
def test_early_threshold_attributes_source_rule_in_each_artifact(artifact):
    finding = next(f for f in findings("b08") if f.artifact == artifact)
    assert finding.rule_ref == "R3"
    assert finding.counterexample.expected_by.location == "R4"


def test_omitted_rule_retains_source_reference():
    finding, = findings("b13")
    assert finding.rule_ref == "R1"
    assert finding.counterexample.expected_by.location == "R1"


def test_exact_quote_from_other_location_is_rejected():
    from reviewer.model_review import FakeModelClient, run_model_review

    bundle = load_bundle(ROOT / "fixtures" / "bundles" / "b01")
    report = review_bundle(bundle)
    first, second = bundle.requirements.requirements[:2]
    import json
    raw = json.dumps({"observations": [{
        "rule_ref": first.id, "artifact": "requirements",
        "version": bundle.requirements.version, "location": first.id,
        "evidence": second.text, "summary": "Mismatched citation", "correction": "Verify the quote",
    }]})
    run_model_review(bundle, report, FakeModelClient([raw, raw]))
    assert report.status == "INCOMPLETE"
    assert [call.outcome for call in report.provenance.model_calls] == ["invalid_references"] * 2
    assert not report.findings


def test_oracle_rejects_top_level_execution():
    from oracle.interpreters import run_code

    with pytest.raises(ValueError, match="undecorated fixture"):
        run_code("raise RuntimeError('executed')\ndef route(ticket): return 'normal'", "route",
                 {"wait_minutes": 0})
