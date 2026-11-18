"""Crash-only check on held-out bundles: they load, parse and evaluate without raising.

Deliberately asserts NOTHING about routing correctness or labels: the held-out
split is not used to tune the reviewer. Any change made because of a held-out
observation would have to be logged in docs/dataset.md as development evidence.
"""
from pathlib import Path

import pytest
import yaml

from reviewer.effective import effective_from_bundle
from reviewer.loader import load_bundle
from reviewer.parse_code import lower_code
from reviewer.parse_spec import lower_spec
from reviewer.parse_workflow import lower_workflow
from reviewer.schemas import Ticket

ROOT = Path(__file__).resolve().parents[1]
HELD = [p.stem for p in sorted((ROOT / "labels").glob("b*.yaml")) if yaml.safe_load(p.read_text())["split"] == "heldout"]
PROBE = [Ticket(category=c, urgent=u, tier=t, wait_minutes=w)
         for c in (None, "spam", "other") for u in (False, True) for t in ("standard", "vip") for w in (0, 29, 30, 31, 1800)]


@pytest.mark.parametrize("bid", HELD)
def test_heldout_bundle_parses_and_evaluates_without_crashing(bid):
    b = load_bundle(ROOT / "fixtures" / "bundles" / bid)
    assert b.load_issues == []
    irs = [effective_from_bundle(b)]
    irs += [lower_spec(b.spec)] if b.spec else []
    irs += [lower_workflow(b.workflow)] if b.workflow else []
    irs += [lower_code(s) for s in b.code]
    for x in irs:
        target = getattr(x, "ir", None) or getattr(x, "decision_list", None)
        if target is not None:
            for t in PROBE:
                target.evaluate(t)
