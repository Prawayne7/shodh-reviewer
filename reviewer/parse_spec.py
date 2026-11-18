"""Lower a ``Spec`` to a ``DecisionList`` (Step 5).

Problems stay visible: a rule or exception with a malformed condition, unknown
destination or ambiguous order becomes *unknown* (tickets that reach it are
excluded from comparison) and gets a coverage entry. Collection-level problems
(duplicate ids or orders) make the whole spec unreviewed.
"""
from __future__ import annotations

from collections import defaultdict

from .ir import (
    SIZE_LIMITS, DecisionList, ExceptionIR, Lowered, RuleIR, coverage_status, lower_schema_cond,
)
from .schemas import CoverageEntry, EvidenceLocation, Issue, RouteAction, Spec, ordered

COLLECTION_CODES = {"U-DUP-ID", "U-ORDER-AMBIG"}


def _entry(item: str, codes: list[str], reasons: list[str], ev: EvidenceLocation | None) -> CoverageEntry:
    return CoverageEntry(item=item, kind="spec", status=coverage_status(codes),
                         codes=sorted(set(codes)), reason="; ".join(reasons), evidence=ev)


def lower_spec(spec: Spec) -> Lowered:
    issues = spec.issues()
    by_loc: dict[str, list[Issue]] = defaultdict(list)
    for i in issues:
        by_loc[i.evidence.location if i.evidence else "document"].append(i)

    doc_level = by_loc.get("document", [])
    if len(spec.rules) > SIZE_LIMITS["rules"]:
        return Lowered(None, [_entry("spec", ["U-SIZE-LIMIT"], [f"{len(spec.rules)} rules; limit {SIZE_LIMITS['rules']}"], spec.evidence)], issues)
    blocking = [i for i in doc_level if i.code in COLLECTION_CODES]
    if blocking:
        return Lowered(None, [_entry("spec", [i.code for i in blocking], [i.message for i in blocking], spec.evidence)], issues)

    declared = spec.units.get("wait")
    coverage: list[CoverageEntry] = []
    rules: list[RuleIR] = []
    for r in ordered(spec.rules):
        codes = [i.code for i in by_loc.get(r.evidence.location, [])]
        reasons = [i.message for i in by_loc.get(r.evidence.location, [])]
        cond, probs = lower_schema_cond(r.when, declared)
        codes += [c for c, _ in probs if c not in codes]
        reasons += [m for _, m in probs]
        rule = RuleIR(id=r.id, evidence=r.evidence, cond=cond, route=r.route, status="ok" if not codes else "unknown")
        for e in ordered(r.exceptions):
            ecodes = [i.code for i in by_loc.get(e.evidence.location, [])]
            ereasons = [i.message for i in by_loc.get(e.evidence.location, [])]
            econd, eprobs = lower_schema_cond(e.when, declared)
            ecodes += [c for c, _ in eprobs if c not in ecodes]
            ereasons += [m for _, m in eprobs]
            skip = e.action == "skip"
            route = e.action.route if isinstance(e.action, RouteAction) else None
            rule.exceptions.append(ExceptionIR(id=e.evidence.location, evidence=e.evidence, cond=econd, skip=skip,
                                               route=route, status="ok" if not ecodes else "unknown"))
            if ecodes:
                coverage.append(_entry(e.evidence.location, ecodes, ereasons, e.evidence))
        if codes:
            coverage.append(_entry(r.id, codes, reasons, r.evidence))
        rules.append(rule)

    default_ev = EvidenceLocation(artifact="spec", version=spec.version, location="default")
    default_codes = [i.code for i in doc_level if i.code == "U-DEST-UNKNOWN"]
    if default_codes:
        coverage.append(_entry("default", default_codes, [i.message for i in doc_level], default_ev))
    dl = DecisionList(rules=rules, default=spec.default, default_evidence=default_ev,
                      default_status="unknown" if default_codes else "ok", no_default=None)
    return Lowered(dl, coverage, issues)
