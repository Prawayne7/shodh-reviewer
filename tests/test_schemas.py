"""Step 3 contract tests: conditions, evidence, structural issues, report invariants."""
import copy
import json
import shutil
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from reviewer.loader import load_bundle
from reviewer.schemas import (
    AllOf, Bundle, CheckResult, Cond, Counterexample, Coverage, CoverageEntry,
    EvidenceItem, EvidenceLocation, Finding, Leaf, Not, Provenance, Report,
    Spec, Ticket, Workflow, iter_leaves, ordered,
)

EXAMPLE = Path(__file__).parent / "data" / "example_bundle"


class _Holder(BaseModel):
    when: Cond


def cond(raw):
    return _Holder.model_validate({"when": raw}).when


# ---- conditions -------------------------------------------------------------
def test_nested_condition_parses_and_walks_leaves():
    c = cond({"all": [{"field": "urgent", "op": "eq", "value": True},
                      {"any": [{"field": "tier", "op": "eq", "value": "vip"},
                               {"not": {"field": "category", "op": "eq", "value": "spam"}}]}]})
    assert isinstance(c, AllOf)
    assert [l.field for l in iter_leaves(c)] == ["urgent", "tier", "category"]


@pytest.mark.parametrize("bad", [
    {"all": []},                                                   # empty combinator
    {"all": [{"field": "urgent", "op": "eq", "value": True}], "field": "x", "op": "eq"},  # mixed shapes
    {"field": "wait", "op": "ge", "value": 30, "bogus": 1},        # unknown key
    {"field": "wait"},                                             # missing op
])
def test_malformed_condition_is_rejected(bad):
    with pytest.raises(ValidationError):
        cond(bad)


def test_value_types_are_not_coerced():
    # "30" stays a string: it is later reported as U-LEAF-SHAPE, never silently read as 30.
    leaf = cond({"field": "wait", "op": "ge", "value": "30", "unit": "minutes"})
    assert leaf.value == "30" and isinstance(leaf.value, str)


# ---- ticket -------------------------------------------------------------------
def test_ticket_normalises_empty_category_to_missing():
    assert Ticket(category="   ", urgent=False, tier="standard", wait_minutes=0).category is None
    assert Ticket(category=" spam ", urgent=False, tier="standard", wait_minutes=0).category == "spam"


@pytest.mark.parametrize("kw", [
    dict(category=3, urgent=False, tier="standard", wait_minutes=0),
    dict(category=None, urgent="yes", tier="standard", wait_minutes=0),
    dict(category=None, urgent=False, tier="gold", wait_minutes=0),
    dict(category=None, urgent=False, tier="vip", wait_minutes=-1),
    dict(category=None, urgent=False, tier="vip", wait_minutes=1.5),
])
def test_invalid_ticket_rejected(kw):
    with pytest.raises(ValidationError):
        Ticket(**kw)


# ---- example bundle, evidence, label leakage -----------------------------------
def test_example_bundle_is_clean_and_evidence_uses_ids():
    b = load_bundle(EXAMPLE)
    assert b.structural_issues() == []
    assert b.spec.rules[1].exceptions[0].evidence.cite() == "spec@spec-v3#S2.E1"
    assert b.workflow.nodes[2].branches[1].evidence.cite() == "workflow@wf-v2#n3.b2"
    assert b.requirements.requirements[1].evidence.cite() == "requirements@req-v1#R2"
    assert b.code[0].evidence.cite() == "code:route.py@c-v1#L1-L11"
    assert b.approvals.records[0].evidence.cite() == "approvals@unversioned#A1"


def test_shuffling_serialized_records_does_not_change_citations_or_order():
    raw = json.loads(json.dumps(load_bundle(EXAMPLE).spec.model_dump(by_alias=True, mode="json")))
    shuffled = copy.deepcopy(raw)
    shuffled["rules"].reverse()
    a, b = Spec.model_validate(raw), Spec.model_validate(shuffled)
    assert [r.id for r in ordered(a.rules)] == [r.id for r in ordered(b.rules)] == ["S1", "S2", "S3"]
    assert {r.id: r.evidence.cite() for r in a.rules} == {r.id: r.evidence.cite() for r in b.rules}


def test_a_leaked_label_is_a_schema_error():
    with pytest.raises(ValidationError):
        Bundle.model_validate({"id": "b01", "expected_status": "PASS"})


# ---- structural issues stay visible ---------------------------------------------
def spec_with(mutate):
    raw = load_bundle(EXAMPLE).spec.model_dump(by_alias=True, mode="json")
    for r in raw["rules"]:
        r["evidence"] = None
        for e in r["exceptions"]:
            e["evidence"] = None
    raw["evidence"] = None
    mutate(raw)
    return Spec.model_validate(raw)


def codes(spec):
    return {i.code for i in spec.issues()}


def test_duplicate_order_and_id_are_flagged_not_raised():
    def m(raw):
        raw["rules"][2]["order"] = 2
        raw["rules"][2]["id"] = "S1"
    assert {"U-ORDER-AMBIG", "U-DUP-ID"} <= codes(spec_with(m))


def test_unknown_field_op_and_bad_shapes_are_flagged():
    def m(raw):
        raw["rules"][0]["when"] = {"field": "priority", "op": "eq", "value": 1}
        raw["rules"][1]["when"] = {"field": "urgent", "op": "contains", "value": True}
        raw["rules"][2]["when"] = {"field": "wait", "op": "ge", "value": "30", "unit": "minutes"}
    assert {"U-UNKNOWN-FIELD", "U-UNKNOWN-OP", "U-LEAF-SHAPE"} <= codes(spec_with(m))


def test_wait_without_unit_is_ambiguous_unless_artifact_declares_one():
    def no_unit(raw):
        raw["units"] = {}
    assert "U-AMBIG-UNIT" in codes(spec_with(no_unit))
    assert "U-AMBIG-UNIT" not in codes(spec_with(lambda raw: None))  # units: {wait: minutes} declared
    def days(raw):
        raw["rules"][2]["when"]["unit"] = "days"
    assert "U-AMBIG-UNIT" in codes(spec_with(days))


def test_empty_category_literal_and_unknown_destination_are_flagged():
    def m(raw):
        raw["rules"][2]["exceptions"][0]["when"] = {"field": "category", "op": "eq", "value": "  "}
        raw["rules"][0]["route"] = "billing_queue"
    assert {"U-EMPTY-LITERAL", "U-DEST-UNKNOWN"} <= codes(spec_with(m))


def workflow_with(mutate):
    raw = load_bundle(EXAMPLE).workflow.model_dump(by_alias=True, mode="json")
    mutate(raw)
    return Workflow.model_validate(raw)


def test_workflow_structure_problems_are_flagged():
    def else_not_last(raw):
        raw["nodes"][2]["branches"][2]["order"] = 1
        raw["nodes"][2]["branches"][0]["order"] = 3
    assert "U-WF-STRUCTURE" in {i.code for i in workflow_with(else_not_last).issues()}

    def dangling(raw):
        raw["nodes"][0]["branches"][1]["else"] = "n99"
    assert "U-WF-STRUCTURE" in {i.code for i in workflow_with(dangling).issues()}

    def no_start(raw):
        raw["start"] = "n0"
    assert "U-WF-STRUCTURE" in {i.code for i in workflow_with(no_start).issues()}


def test_malformed_branch_or_node_shapes_raise():
    with pytest.raises(ValidationError):
        workflow_with(lambda raw: raw["nodes"][0]["branches"][0].update({"else": "n2"}))
    with pytest.raises(ValidationError):
        workflow_with(lambda raw: raw["nodes"][3].update({"destination": None}))


# ---- findings and report invariants ------------------------------------------------
LOC = EvidenceLocation(artifact="spec", version="spec-v3", location="S3")
TICKET = Ticket(category="billing", urgent=False, tier="standard", wait_minutes=25)


def counterexample():
    return Counterexample(ticket=TICKET, expected_route="normal_queue", actual_route="escalation",
                          artifact="spec", expected_by=EvidenceLocation(artifact="requirements", version="req-v1", location="R3"),
                          actual_by=LOC)


def finding(severity="blocker", source="deterministic", with_cx=True, kind="threshold_mismatch"):
    return Finding(id="F1", kind=kind, severity=severity, source=source, rule_ref="R3",
                   artifact="spec", version="spec-v3", location="S3", summary="threshold differs",
                   evidence=[EvidenceItem(location=LOC)], correction="use 30",
                   counterexample=counterexample() if with_cx else None)


def report(status, findings=(), entries=(), checks=()):
    return Report(bundle_id="b01", status=status, summary="s", findings=list(findings),
                  coverage=Coverage(entries=list(entries)), check_results=list(checks),
                  provenance=Provenance(reviewer_version="test"))


OK_CHECK = CheckResult(name="semantic_spec", required=True, status="passed", mode="exhaustive")
UNSUPPORTED = CoverageEntry(item="R9", kind="requirement", status="unsupported", codes=["U-PROSE"])


def test_counterexample_must_differ():
    with pytest.raises(ValidationError):
        Counterexample(ticket=TICKET, expected_route="a", actual_route="a", artifact="spec", expected_by=LOC)


def test_finding_rules():
    finding()  # valid behavioural blocker
    with pytest.raises(ValidationError):
        finding(with_cx=False)                       # behavioural blocker without counterexample
    with pytest.raises(ValidationError):
        finding(source="model")                      # a model can never raise a blocker
    finding(kind="structure_defect", with_cx=False)  # structural blocker needs no ticket
    finding(severity="warning", source="model", with_cx=False)
    with pytest.raises(ValidationError):
        Finding(id="F", kind="structure_defect", severity="info", artifact="a", version="v", location="l",
                summary="s", evidence=[], correction="c")  # evidence is mandatory


def test_pass_requires_everything_complete():
    report("PASS", checks=[OK_CHECK])
    with pytest.raises(ValidationError):
        report("PASS", findings=[finding()], checks=[OK_CHECK])
    with pytest.raises(ValidationError):
        report("PASS", entries=[UNSUPPORTED], checks=[OK_CHECK])
    for bad in ("failed", "error", "skipped"):
        with pytest.raises(ValidationError):
            report("PASS", checks=[CheckResult(name="model_review", required=True, status=bad, mode="model")])
    report("PASS", checks=[CheckResult(name="extra", required=False, status="skipped", mode="static")])


def test_changes_required_needs_a_blocker_even_with_unreviewed_scope():
    report("CHANGES_REQUIRED", findings=[finding()], entries=[UNSUPPORTED])
    with pytest.raises(ValidationError):
        report("CHANGES_REQUIRED", findings=[finding(severity="warning", with_cx=False)])


def test_incomplete_means_something_prevented_full_review_and_no_blocker():
    report("INCOMPLETE", entries=[UNSUPPORTED])
    report("INCOMPLETE", checks=[CheckResult(name="model_review", required=True, status="error", mode="model")])
    with pytest.raises(ValidationError):
        report("INCOMPLETE", findings=[finding()], entries=[UNSUPPORTED])
    with pytest.raises(ValidationError):
        report("INCOMPLETE", checks=[OK_CHECK])      # nothing is actually incomplete


def test_report_json_round_trip():
    r = report("CHANGES_REQUIRED", findings=[finding()], checks=[OK_CHECK])
    again = Report.model_validate_json(r.model_dump_json())
    assert again == r
    assert again.findings[0].counterexample.ticket.wait_minutes == 25
