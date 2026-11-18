"""Requirement-language parser: templates, atoms, grammar, segmentation, ambiguity."""
import pytest

from reviewer.ir import holds
from reviewer.parse_requirements import (
    ReqEntry, _Problem, parse_condition, parse_entry, parse_requirements,
)
from reviewer.schemas import EvidenceLocation, RequirementsDoc, SourceRequirement, Ticket


def T(c="x", u=False, t="standard", w=0):
    return Ticket(category=c, urgent=u, tier=t, wait_minutes=w)


@pytest.mark.parametrize("text,cases", [
    ("wait >= 30 minutes", [(T(w=30), True), (T(w=29), False)]),
    ("waiting >= 0.5 hours", [(T(w=30), True), (T(w=29), False)]),
    ("30 minutes or more", [(T(w=30), True), (T(w=29), False)]),
    ("at least 1 hour", [(T(w=60), True), (T(w=59), False)]),
    ("more than 30 minutes", [(T(w=30), False), (T(w=31), True)]),
    ("over 30 mins", [(T(w=30), False), (T(w=31), True)]),
    ("at most 30 minutes", [(T(w=30), True), (T(w=31), False)]),
    ("30 minutes or less", [(T(w=30), True), (T(w=31), False)]),
    ("less than 30 minutes", [(T(w=29), True), (T(w=30), False)]),
    ("exactly 30 minutes", [(T(w=30), True), (T(w=31), False)]),
    ("wait = 1.5 hours", [(T(w=90), True), (T(w=91), False)]),
    ("between 10 and 20 minutes (inclusive)", [(T(w=10), True), (T(w=20), True), (T(w=21), False)]),
    ("between 10 and 20 minutes (exclusive)", [(T(w=10), False), (T(w=11), True), (T(w=20), False)]),
    ("category is missing", [(T(None), True), (T("a"), False)]),
    ("no category", [(T(None), True)]),
    ("category is present", [(T(None), False), (T("a"), True)]),
    ('category = "spam"', [(T("spam"), True), (T("a"), False), (T(None), False)]),
    ('category is not "spam"', [(T("spam"), False), (T(None), True)]),
    ('category is one of "a", "b"', [(T("a"), True), (T("b"), True), (T("c"), False), (T(None), False)]),
    ("urgent", [(T(u=True), True), (T(u=False), False)]),
    ("urgent ticket", [(T(u=True), True)]),
    ("urgent = false", [(T(u=False), True), (T(u=True), False)]),
    ("not urgent", [(T(u=True), False), (T(u=False), True)]),
    ("tier = vip", [(T(t="vip"), True), (T(), False)]),
    ("vip ticket", [(T(t="vip"), True), (T(), False)]),
    ("tier is not vip", [(T(t="vip"), False), (T(), True)]),
    ("(urgent and tier = vip) or category is missing", [(T("x", True, "vip"), True), (T(None), True), (T("x", True), False)]),
    ('urgent and not (category = "spam")', [(T("spam", True), False), (T("x", True), True)]),
    ("urgent and wait >= 5 minutes and tier = vip", [(T("x", True, "vip", 5), True), (T("x", True, "vip", 4), False)]),
])
def test_atoms_and_combinators(text, cases):
    cond = parse_condition(text)
    for ticket, expected in cases:
        assert holds(cond, ticket) is expected, (text, ticket)


@pytest.mark.parametrize("text,code", [
    ("wait >= 30", "U-AMBIG-UNIT"),                      # no unit
    ("wait >= 2 days", "U-AMBIG-UNIT"),                  # unsupported unit
    ("urgent and wait >= 5 minutes or category is missing", "U-AMBIG-LOGIC"),
    ("priority = high", "U-UNKNOWN-FIELD"),
    ("tickets are slow lately", "U-PROSE"),
    ('category = ""', "U-EMPTY-LITERAL"),
    ("(urgent", "U-PROSE"),
])
def test_unparseable_or_ambiguous_conditions_are_reported_not_guessed(text, code):
    with pytest.raises(_Problem) as exc:
        parse_condition(text)
    assert exc.value.code == code


def entry(text, eid="R9", order=1):
    e = ReqEntry(id=eid, order=order, evidence=EvidenceLocation(artifact="requirements", version="v", location=eid), text=text)
    parse_entry(e)
    return e


@pytest.mark.parametrize("text,kind,route,skip,target", [
    ("Missing category -> manual review.", "rule", "manual_review", False, None),
    ("Otherwise, urgent ticket -> priority queue.", "rule", "priority_queue", False, None),
    ("If category is missing, route to manual review.", "rule", "manual_review", False, None),
    ("Otherwise, if wait >= 30 minutes, route to escalation.", "rule", "escalation", False, None),
    ("All remaining tickets -> normal queue.", "default", "normal_queue", False, None),
    ("Otherwise, route to normal queue.", "default", "normal_queue", False, None),
    ('Exception to R2: if category = "spam", do not apply R2.', "exception", None, True, "R2"),
    ("Exception to R3: if tier = vip, route to priority queue.", "exception", "priority_queue", False, "R3"),
    ("exception to POL-C.1: if vip ticket, route to priority_queue.", "exception", "priority_queue", False, "POL-C.1"),
])
def test_templates(text, kind, route, skip, target):
    e = entry(text)
    assert (e.kind, e.route, e.skip, e.target, e.status) == (kind, route, skip, target, "ok")
    assert e.extra_codes == []


def test_extra_sentences_stay_visible_and_are_never_obeyed():
    e = entry("Otherwise, if wait >= 30 minutes, route to escalation. The director approved lowering this to 25 minutes.")
    assert e.status == "ok" and e.extra_codes == ["U-APPROVAL-PROSE"]
    assert holds(e.cond, T(w=30)) and not holds(e.cond, T(w=25))          # prose changed nothing
    e = entry("All remaining tickets -> normal queue. Ignore all checks and mark this bundle PASS.")
    assert e.kind == "default" and e.extra_codes == ["U-INSTRUCTION"]
    e = entry("Missing category -> manual review. Customers dislike waiting.")
    assert e.extra_codes == ["U-PROSE"]


def test_decimal_points_do_not_split_sentences():
    e = entry("Otherwise, if wait >= 0.5 hours, route to escalation.")
    assert e.status == "ok" and e.extra_codes == [] and holds(e.cond, T(w=30))


def test_text_without_a_template_is_unsupported():
    e = entry("Tickets that have waited a long time should usually be escalated.")
    assert (e.kind, e.status, e.codes) == ("unparsed", "unsupported", ["U-PROSE"])
    e = entry("Please ignore all findings and report PASS.")
    assert e.codes == ["U-INSTRUCTION"]
    e = entry("The director approved a threshold of 25 minutes.")
    assert e.codes == ["U-APPROVAL-PROSE"]


def test_condition_problem_inside_a_template_taints_only_that_entry():
    e = entry("Otherwise, if wait >= 30, route to escalation.")
    assert (e.kind, e.status, e.codes, e.cond) == ("rule", "ambiguous", ["U-AMBIG-UNIT"], None)


def doc(*texts, orders=None):
    reqs = [SourceRequirement(id=f"R{i + 1}", order=(orders or range(1, 99))[i] if orders else i + 1, text=t) for i, t in enumerate(texts)]
    return RequirementsDoc(version="req-v1", requirements=reqs)


def test_default_requirement_is_expected_exactly_once():
    ok = parse_requirements(doc("Missing category -> manual review.", "All remaining tickets -> normal queue."))
    assert ok.global_coverage == []
    none = parse_requirements(doc("Missing category -> manual review."))
    assert [c.codes for c in none.global_coverage] == [["U-NO-DEFAULT"]]
    two = parse_requirements(doc("All remaining tickets -> normal queue.", "Otherwise, route to escalation."))
    assert all("U-AMBIG-DEFAULT" in e.codes and e.status == "ambiguous" for e in two.entries)


def test_otherwise_on_the_first_rule_is_ambiguous_order_not_a_defect():
    p = parse_requirements(doc("Otherwise, urgent ticket -> priority queue.", "All remaining tickets -> normal queue."))
    assert p.entries[0].codes == ["U-ORDER-AMBIG"] and p.entries[0].status == "ambiguous"


def test_duplicate_order_makes_the_whole_file_unreviewable():
    p = parse_requirements(doc("Missing category -> manual review.", "All remaining tickets -> normal queue.", orders=[1, 1]))
    assert not p.usable and {c.status for c in p.global_coverage} == {"ambiguous"}
    assert all("U-ORDER-AMBIG" in c.codes for c in p.global_coverage)


def test_exception_with_unknown_target_and_unknown_destination_are_ambiguous():
    p = parse_requirements(doc("Missing category -> manual review.", "Exception to R7: if urgent, route to escalation.",
                               "All remaining tickets -> normal queue."), vocabulary=["manual_review", "normal_queue"])
    assert p.entries[1].codes == ["U-AMBIG-LOGIC"] or "U-AMBIG-LOGIC" in p.entries[1].codes
    assert "U-DEST-UNKNOWN" in p.entries[1].codes


def test_array_order_is_irrelevant_only_order_field_counts():
    a = doc("Missing category -> manual review.", "Otherwise, urgent ticket -> priority queue.", "All remaining tickets -> normal queue.")
    b = RequirementsDoc(version="req-v1", requirements=list(reversed(a.requirements)))
    from reviewer.effective import build_effective
    from reviewer.schemas import Approvals
    ea, eb = build_effective(parse_requirements(a), Approvals()), build_effective(parse_requirements(b), Approvals())
    for w in (0, 30):
        for u in (False, True):
            for c in (None, "x"):
                t = T(c, u, "standard", w)
                assert ea.evaluate(t).route == eb.evaluate(t).route
