"""Step 5 cross-check against the independent oracle (dev bundles only).

Every parsed artifact (spec, workflow, code) must route every ticket of the
dense oracle grid exactly like the oracle's own interpreters, which share no
code with ``reviewer/``. The effective requirements must route like the
hand-written policy with the labelled threshold, except for tickets that reach
unreviewed content, which must be UNKNOWN and never a wrong destination.

Held-out bundles are deliberately NOT used here (no tuning on them); see
tests/test_step5_heldout_smoke.py for the crash-only check and docs/dataset.md
for the development-evidence log.
"""
from pathlib import Path

import pytest
import yaml

from oracle.interpreters import load_raw, runners
from oracle.policy import grid, key, support_v1
from reviewer.effective import effective_from_bundle
from reviewer.ir import UNKNOWN
from reviewer.loader import load_bundle
from reviewer.parse_code import lower_code
from reviewer.parse_spec import lower_spec
from reviewer.parse_workflow import lower_workflow
from reviewer.schemas import Ticket

ROOT = Path(__file__).resolve().parents[1]
LABELS = {p.stem: yaml.safe_load(p.read_text()) for p in sorted((ROOT / "labels").glob("b*.yaml"))}
DEV = [b for b, l in LABELS.items() if l["split"] == "dev"]
TICKETS = [(t, Ticket(**t)) for t in grid()]


def _same(ir, oracle_fn, what):
    bad = []
    for raw, ticket in TICKETS:
        got, want = ir.evaluate(ticket).route, oracle_fn(raw)
        if got != want:
            bad.append((raw, got, want))
            if len(bad) >= 3:
                break
    assert not bad, f"{what}: parsed IR disagrees with the oracle interpreter: {bad}"


@pytest.mark.parametrize("bid", DEV)
def test_parsed_artifacts_route_like_the_oracle_interpreters(bid):
    bundle = load_bundle(ROOT / "fixtures" / "bundles" / bid)
    run = runners(load_raw(ROOT / "fixtures" / "bundles" / bid))
    if bundle.spec:
        low = lower_spec(bundle.spec)
        if low.ir is not None:
            _same(low.ir, run["spec"], f"{bid} spec")
    if bundle.workflow:
        low = lower_workflow(bundle.workflow)
        if low.ir is not None:
            _same(low.ir, run["workflow"], f"{bid} workflow")
    for snip in bundle.code:
        low = lower_code(snip)
        if low.ir is not None:
            _same(low.ir, run[f"code:{snip.id}"], f"{bid} code {snip.id}")


@pytest.mark.parametrize("bid", DEV)
def test_effective_requirements_match_the_intended_policy(bid):
    label = LABELS[bid]
    eff = effective_from_bundle(load_bundle(ROOT / "fixtures" / "bundles" / bid))
    thr = label["intended_policy"]["wait_threshold_minutes"]
    unknown = 0
    for raw, ticket in TICKETS:
        got = eff.evaluate(ticket).route
        if got is UNKNOWN:
            unknown += 1
            continue
        assert got == support_v1(raw, thr), (bid, raw, got)
    if label["case_type"] != "incomplete":
        assert unknown == 0, (bid, unknown)  # nothing unreviewed in valid or defective bundles


def _cov(eff):
    return {(c.kind, c.item): c.status for c in eff.coverage}


def test_labelled_coverage_is_reproduced_for_dev_incomplete_bundles():
    for bid in DEV:
        label = LABELS[bid]
        eff = effective_from_bundle(load_bundle(ROOT / "fixtures" / "bundles" / bid))
        cov = _cov(eff)
        for want in label["expected_coverage"]:
            if want["kind"] == "requirement":
                assert cov.get(("requirement", want["id"])) == want["status"], (bid, want, cov)


def test_valid_dev_bundles_have_no_unreviewed_requirements_and_no_unknown_tickets():
    for bid in DEV:
        if LABELS[bid]["case_type"] != "valid":
            continue
        eff = effective_from_bundle(load_bundle(ROOT / "fixtures" / "bundles" / bid))
        blocking = [c for c in eff.coverage if c.status in ("unsupported", "ambiguous", "conflicted", "not_reviewed", "omitted")]
        assert blocking == [], (bid, blocking)
        assert all(eff.evaluate(t).route is not UNKNOWN for _, t in TICKETS), bid


def test_unknown_barrier_blocks_exactly_the_tickets_that_reach_the_unreviewed_rule():
    # b17: R3 is free prose. Tickets decided by R1/R2 stay known; every other ticket is UNKNOWN.
    eff = effective_from_bundle(load_bundle(ROOT / "fixtures" / "bundles" / "b17"))
    for raw, ticket in TICKETS:
        decided_before_r3 = raw["category"] is None or (raw["urgent"] and raw["category"] != "spam")
        d = eff.evaluate(ticket)
        assert (d.route is not UNKNOWN) == decided_before_r3, (raw, d)
        if decided_before_r3:
            assert d.route == support_v1(raw, 30)
