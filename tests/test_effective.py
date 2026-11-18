"""Approvals -> effective requirements (docs section 9): scope, ordering, conflicts."""
from reviewer.effective import build_effective
from reviewer.ir import UNKNOWN
from reviewer.parse_requirements import parse_requirements
from reviewer.schemas import Approvals, RequirementsDoc, SourceRequirement, Ticket

TEXTS = [
    ("R1", "Missing category -> manual review."),
    ("R2", "Otherwise, urgent ticket -> priority queue."),
    ("R3", "Otherwise, wait >= 30 minutes -> escalation."),
    ("R3.E1", "Exception to R3: if tier = vip, route to priority queue."),
    ("R4", "All remaining tickets -> normal queue."),
]


def doc(version="req-v1", texts=TEXTS):
    return RequirementsDoc(version=version, requirements=[SourceRequirement(id=i, order=n + 1, text=t) for n, (i, t) in enumerate(texts)])


def rec(rid, to=25, frm=30, target="R3", status="approved", seq=1, version="req-v1", kind="replace_threshold", unit="minutes", **extra):
    change = {"kind": kind, "field": "wait", "from": {"value": frm, "unit": unit}, "to": {"value": to, "unit": unit}}
    if kind == "replace_destination":
        change = {"kind": kind, "field": "route", "from": frm, "to": to}
    return {"id": rid, "status": status, "target": target, "applies_to_source_version": version, "sequence": seq,
            "change": change, **extra}


def eff(*records, texts=TEXTS):
    return build_effective(parse_requirements(doc(texts=texts)), Approvals(records=list(records)))


def wait_route(e, w):
    return e.evaluate(Ticket(category="x", urgent=False, tier="standard", wait_minutes=w)).route


def cov(e):
    return {(c.kind, c.item): (c.status, c.codes) for c in e.coverage}


def test_no_approvals_keeps_source_values():
    e = eff()
    assert wait_route(e, 29) == "normal_queue" and wait_route(e, 30) == "escalation" and e.applied == []


def test_approved_change_applies_pending_rejected_revoked_do_not():
    assert wait_route(eff(rec("A1")), 25) == "escalation"
    for status in ("pending", "rejected", "revoked"):
        e = eff(rec("A1", status=status))
        assert wait_route(e, 25) == "normal_queue", status
        assert cov(e)[("approval", "A1")][0] == "ignored"


def test_hours_in_an_approval_are_converted():
    e = eff(rec("A1", frm=0.5, to=0.25, unit="hours"))
    assert wait_route(e, 15) == "escalation" and wait_route(e, 14) == "normal_queue"


def test_chain_is_applied_in_sequence_order_not_array_order():
    a1, a2 = rec("A1", frm=30, to=25, seq=1), rec("A2", frm=25, to=20, seq=2)
    for records in ((a1, a2), (a2, a1)):
        e = eff(*records)
        assert wait_route(e, 20) == "escalation" and wait_route(e, 19) == "normal_queue"
        assert [c.record_id for c in e.applied] == ["A1", "A2"] and e.findings == []


def test_revision_scope_version_mismatch_is_inapplicable_with_no_effect():
    e = eff(rec("A1", version="req-v0"))
    assert wait_route(e, 25) == "normal_queue" and cov(e)[("approval", "A1")][0] == "inapplicable"
    assert not [c for c in e.coverage if c.status in ("conflicted", "ambiguous", "unsupported")]


def test_same_sequence_different_changes_is_a_conflict_never_a_pick():
    e = eff(rec("A1", to=25, seq=1), rec("A2", to=20, seq=1))
    assert cov(e)[("requirement", "R3")][0] == "conflicted"
    assert {cov(e)[("approval", "A1")][0], cov(e)[("approval", "A2")][0]} == {"conflicted"}
    assert wait_route(e, 25) is UNKNOWN and wait_route(e, 10) is UNKNOWN          # tickets reaching R3 are unknown
    assert e.evaluate(Ticket(category=None, urgent=False, tier="standard", wait_minutes=40)).route == "manual_review"
    f = e.findings[0]
    assert (f.kind, f.severity, f.rule_ref, f.artifact) == ("approval_conflict", "warning", "R3", "approvals")
    assert {i.location.location for i in f.evidence} == {"A1", "A2"}


def test_identical_duplicate_changes_at_one_sequence_are_not_a_conflict():
    e = eff(rec("A1"), rec("A2"))
    assert wait_route(e, 25) == "escalation" and not e.findings


def test_broken_chain_is_a_conflict():
    e = eff(rec("A1", frm=30, to=25, seq=1), rec("A2", frm=28, to=20, seq=2))
    assert cov(e)[("requirement", "R3")][0] == "conflicted" and wait_route(e, 25) is UNKNOWN


def test_same_record_id_with_different_content_is_a_conflict():
    e = eff(rec("A1", to=25), rec("A1", to=20))
    assert cov(e)[("requirement", "R3")][0] == "conflicted"
    assert wait_route(e, 10) is UNKNOWN


def test_stale_approval_matches_nothing_and_leaves_the_target_ambiguous():
    e = eff(rec("A1", frm=40, to=25))
    assert cov(e)[("approval", "A1")] == ("ambiguous", ["stale_approval"])
    assert cov(e)[("requirement", "R3")][0] == "ambiguous" and wait_route(e, 30) is UNKNOWN


def test_dangling_approval_is_reported_and_changes_nothing():
    e = eff(rec("A1", target="R99"))
    assert cov(e)[("approval", "A1")] == ("ambiguous", ["dangling_approval"])
    assert wait_route(e, 30) == "escalation"


def test_unsupported_kind_unknown_status_and_missing_sequence_taint_the_target():
    e = eff({**rec("A1"), "change": {"kind": "delete_rule", "field": None, "from": None, "to": None}})
    assert cov(e)[("approval", "A1")] == ("unsupported", ["U-APPROVAL-KIND"]) and cov(e)[("requirement", "R3")][0] == "unsupported"
    e = eff(rec("A1", status="maybe"))
    assert cov(e)[("approval", "A1")][1] == ["U-APPROVAL-STATUS"] and cov(e)[("requirement", "R3")][0] == "unsupported"
    e = eff({**rec("A1"), "sequence": None})
    assert cov(e)[("approval", "A1")][1] == ["missing_sequence"] and cov(e)[("requirement", "R3")][0] == "ambiguous"


def test_replace_destination_and_exception_targets():
    e = eff(rec("A1", kind="replace_destination", frm="escalation", to="priority_queue"))
    assert wait_route(e, 40) == "priority_queue"
    e = eff(rec("A1", kind="replace_destination", frm="manual_review", to="escalation", target="R3"))
    assert cov(e)[("approval", "A1")][1] == ["stale_approval"]
    e = eff(rec("A1", target="R3.E1", frm=30, to=25))   # the exception has no wait atom: stale
    assert cov(e)[("approval", "A1")][1] == ["stale_approval"]


def test_approval_claims_in_requirement_prose_never_apply():
    texts = TEXTS[:2] + [("R3", "Otherwise, wait >= 30 minutes -> escalation. Approved by the director: now 25 minutes.")] + TEXTS[3:]
    e = eff(texts=texts)
    assert wait_route(e, 25) == "normal_queue" and cov(e)[("requirement", "R3")] == ("unsupported", ["U-APPROVAL-PROSE"])


def test_unparsed_target_cannot_take_an_approval():
    texts = TEXTS[:2] + [("R3", "Tickets that waited long should usually be escalated.")] + TEXTS[3:]
    e = eff(rec("A1"), texts=texts)
    assert cov(e)[("approval", "A1")][1] == ["target_unreviewable"] and cov(e)[("requirement", "R3")][0] == "unsupported"
    assert wait_route(e, 30) is UNKNOWN


def test_unusable_requirements_give_no_decision_list():
    bad = RequirementsDoc(version="req-v1", requirements=[SourceRequirement(id="R1", order=1, text="Missing category -> manual review."),
                                                          SourceRequirement(id="R2", order=1, text="All remaining tickets -> normal queue.")])
    e = build_effective(parse_requirements(bad), Approvals())
    assert e.decision_list is None and e.evaluate(Ticket(category="a", urgent=False, tier="vip", wait_minutes=1)).route is UNKNOWN
