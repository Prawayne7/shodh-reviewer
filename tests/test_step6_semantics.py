from pathlib import Path
import yaml

from reviewer.loader import load_bundle
from reviewer.semantics import review_bundle, representative_waits
from reviewer.status import decide_status

ROOT = Path(__file__).resolve().parents[1]


def test_step6_matches_frozen_expected_statuses_except_model_failure_case():
    for label_path in sorted((ROOT / "labels").glob("b*.yaml")):
        label = yaml.safe_load(label_path.read_text())
        # b19's expected INCOMPLETE is caused by its scripted model failure,
        # which belongs to Stage 3 and is intentionally not run in Step 6.
        if label_path.stem == "b19":
            continue
        report = review_bundle(load_bundle(ROOT / "fixtures" / "bundles" / label_path.stem))
        assert report.status == label["expected_status"], label_path.stem


def test_valid_bundle_has_no_semantic_findings():
    report = review_bundle(load_bundle(ROOT / "fixtures" / "bundles" / "b01"))
    assert report.status == "PASS"
    assert report.findings == []
    assert all(c.status == "passed" for c in report.check_results)


def test_defective_bundle_has_evidenced_counterexample():
    report = review_bundle(load_bundle(ROOT / "fixtures" / "bundles" / "b07"))
    assert report.status == "CHANGES_REQUIRED"
    finding = next(f for f in report.findings if f.severity == "blocker")
    assert finding.counterexample is not None
    assert finding.counterexample.expected_route != finding.counterexample.actual_route
    assert len(finding.evidence) >= 2


def test_wait_partition_contains_threshold_neighbours():
    from fractions import Fraction
    from reviewer.ir import Atom
    class FakeIR:
        def conditions(self):
            return [Atom("wait", "ge", Fraction(25))]
    waits = representative_waits([FakeIR()])
    assert {24, 25, 26}.issubset(set(waits))


def test_category_partition_includes_arbitrary_literals_and_unmatched_value():
    from reviewer.ir import Atom
    from reviewer.semantics import representative_categories, ticket_domain

    class FakeIR:
        def conditions(self):
            return [Atom("category", "eq", "enterprise"), Atom("category", "ne", "regulated")]

    categories = representative_categories([FakeIR()])
    assert None in categories
    assert "enterprise" in categories
    assert "regulated" in categories
    assert any(value not in {None, "enterprise", "regulated", "billing", "spam", "other"}
               for value in categories)
    assert {"enterprise", "regulated"}.issubset({t.category for t in ticket_domain([FakeIR()])})


def test_category_partition_has_a_fresh_unmatched_value_even_if_literal_collides():
    from reviewer.ir import Atom
    from reviewer.semantics import representative_categories

    class FakeIR:
        def conditions(self):
            return [Atom("category", "eq", "__reviewer_unmatched_category__")]

    values = representative_categories([FakeIR()])
    assert "__reviewer_unmatched_category__" in values
    assert any(x is not None and x != "__reviewer_unmatched_category__" for x in values)


def test_semantic_comparison_catches_unlisted_category_literal():
    from reviewer.ir import Atom, DecisionList, RuleIR
    from reviewer.schemas import EvidenceLocation
    from reviewer.semantics import _first_difference, ticket_domain

    ev = EvidenceLocation(artifact="requirements", version="v1", location="enterprise-rule")
    expected = DecisionList(
        rules=[RuleIR(id="enterprise", evidence=ev, cond=Atom("category", "eq", "enterprise"), route="priority")],
        default="normal", default_evidence=ev,
    )
    actual = DecisionList(rules=[], default="normal", default_evidence=ev)
    domain = ticket_domain([expected, actual])
    diff = _first_difference(expected, actual, domain)
    assert diff is not None
    ticket, exp, got = diff
    assert ticket.category == "enterprise"
    assert exp.route == "priority"
    assert got.route == "normal"
