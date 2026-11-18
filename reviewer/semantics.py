"""Step 6: deterministic semantic comparison across effective requirements and artifacts.

The checker never executes submitted Python. It compares the IRs produced by
Steps 1-5 over a boundary-partitioned ticket domain. For the supported language
(all conditions are combinations of equality and numeric order comparisons),
these representatives cover every truth-value region induced by the thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import product
from typing import Any, Iterable

from .effective import effective_from_bundle
from .ir import UNKNOWN, DecisionList, iter_atoms, wait_thresholds
from .parse_code import lower_code
from .parse_requirements import parse_requirements
from .parse_spec import lower_spec
from .parse_workflow import lower_workflow
from .status import decide_status
from .schemas import (
    Bundle, CheckResult, Counterexample, Coverage, CoverageEntry, EvidenceItem,
    EvidenceLocation, Finding, Provenance, Report, Ticket,
)

BASE_CATEGORIES = ("billing", "spam", "other")
TIERS = ("standard", "vip")


def _conditions(ir: Any) -> Iterable[Any]:
    if ir is not None and hasattr(ir, "conditions"):
        yield from ir.conditions()


def _thresholds(irs: Iterable[Any]) -> set[Fraction]:
    out: set[Fraction] = set()
    for ir in irs:
        for cond in _conditions(ir):
            out.update(wait_thresholds(cond))
    return out


def representative_waits(irs: Iterable[Any]) -> list[int]:
    """Integer representatives for every interval/equality cell in wait>=0."""
    values = {0, 1, 29, 30, 31, 59, 60, 61, 1799, 1800, 1801}
    for threshold in _thresholds(irs):
        # All supported ticket waits are non-negative integers. Include the
        # integer immediately below/at/above each rational boundary.
        floor = threshold.numerator // threshold.denominator
        for n in (floor - 1, floor, floor + 1, floor + 2):
            if n >= 0:
                values.add(n)
    return sorted(values)


def representative_categories(irs: Iterable[Any]) -> list[str | None]:
    """Represent every category equality partition, including an unmatched value.

    Category is an open string domain. A fixed sample such as billing/spam/other
    is unsound because a rule may compare against any literal (for example,
    ``category == \"enterprise\"``). Include every literal found in every IR
    plus one value guaranteed not to equal any literal.
    """
    literals: set[str] = set()
    for ir in irs:
        for cond in _conditions(ir):
            for atom in iter_atoms(cond):
                if atom.field == "category" and isinstance(atom.value, str):
                    literals.add(atom.value)
    unmatched = "__reviewer_unmatched_category__"
    while unmatched in literals:
        unmatched += "_"
    ordered = [None]
    ordered.extend(sorted(literals))
    ordered.extend(x for x in BASE_CATEGORIES if x not in literals)
    ordered.append(unmatched)
    return ordered


def ticket_domain(irs: Iterable[Any]) -> list[Ticket]:
    irs = list(irs)
    waits = representative_waits(irs)
    categories = representative_categories(irs)
    return [Ticket(category=c, urgent=u, tier=t, wait_minutes=w)
            for c, u, t, w in product(categories, (False, True), TIERS, waits)]


def _coverage_unknown(ir: Any, coverage: list[CoverageEntry]) -> bool:
    return ir is None or any(c.status in {"unsupported", "ambiguous", "conflicted", "not_reviewed"} for c in coverage)


def _first_difference(expected_ir: Any, actual_ir: Any, tickets: list[Ticket]):
    for ticket in tickets:
        expected = expected_ir.evaluate(ticket)
        if expected.route is UNKNOWN:
            continue
        actual = actual_ir.evaluate(ticket)
        if actual.route is UNKNOWN:
            continue
        if expected.route != actual.route:
            return ticket, expected, actual
    return None


def _threshold_location(fallback_ir, stable_ir, fallback, stable, ticket, waits):
    """Corroborate a displaced fallback by varying only the wait input.

    Report a rule only if there is an unambiguous agreement on the stable
    route while its deciding evidence stays fixed. Omitted rules have no
    such witness and retain the fallback and original requirement evidence.
    """
    locations = {}
    for wait in waits:
        probe = ticket.model_copy(update={"wait_minutes": wait})
        candidate = fallback_ir.evaluate(probe)
        if (candidate.by is not None and candidate.by != fallback.by
                and candidate.route == stable.route
                and stable_ir.evaluate(probe) == stable):
            loc = candidate.by
            locations[(loc.artifact, loc.version, loc.location)] = (loc, probe)
    if len(locations) == 1:
        return next(iter(locations.values()))
    return None


def _finding(bundle_id: str, artifact_name: str, ticket: Ticket, expected: Any, actual: Any, source_hint=None, defect_hint=None) -> Finding:
    expected_loc = expected.by or EvidenceLocation(artifact="requirements", version="unknown", location="effective-policy")
    actual_loc = actual.by or EvidenceLocation(artifact=artifact_name, version="unknown", location="decision-output")
    kind = "missing_branch" if actual.route is None else "wrong_destination"
    if expected.route is not None and actual.route is not None and expected.route != actual.route:
        kind = "unclassified_mismatch"
    ce = Counterexample(ticket=ticket, expected_route=expected.route, actual_route=actual.route,
                        artifact=artifact_name, expected_by=expected_loc, actual_by=actual_loc)
    defect_loc = defect_hint[0] if defect_hint else actual_loc
    rule_loc = source_hint[0] if source_hint else expected_loc
    evidence = [EvidenceItem(location=expected_loc, excerpt=f"Expected route: {expected.route}"),
                EvidenceItem(location=actual_loc, excerpt=f"Actual route: {actual.route}")]
    for hint in (source_hint, defect_hint):
        if hint:
            loc, probe = hint
            evidence.append(EvidenceItem(location=loc, excerpt=(
                f"Corroborating decision with the same ticket at wait={probe.wait_minutes} minutes."
            )))
    return Finding(
        id=f"semantic:{bundle_id}:{artifact_name}:{ticket.wait_minutes}:{ticket.category}:{ticket.urgent}:{ticket.tier}",
        kind=kind, severity="blocker", source="deterministic", rule_ref=rule_loc.location,
        artifact=defect_loc.artifact, version=defect_loc.version, location=defect_loc.location,
        summary=(f"{artifact_name} routes a supported ticket to {actual.route!r}; "
                 f"the effective requirement routes it to {expected.route!r}."),
        evidence=evidence,
        correction=(f"Align {defect_loc.location} with source rule {rule_loc.location} "
                    f"so this ticket follows route {expected.route!r}."),
        counterexample=ce,
    )


def review_bundle(bundle: Bundle) -> Report:
    """Run Step 6 and return the validated report-v1 model."""
    findings: list[Finding] = []
    coverage_entries: list[CoverageEntry] = []
    checks: list[CheckResult] = []

    # Step 5 already provides parsers and approval resolution. Reuse those exact
    # contracts rather than re-parsing or inferring meaning from IDs.
    if bundle.requirements is None:
        eff = None
        coverage_entries.append(CoverageEntry(item="requirements", kind="requirement", status="not_reviewed",
            codes=["U-MISSING-ARTIFACT"], reason="source requirements are missing"))
    else:
        parsed = parse_requirements(bundle.requirements, bundle.spec.destinations if bundle.spec else None)
        eff = effective_from_bundle(bundle)
        coverage_entries.extend(parsed.global_coverage)
        coverage_entries.extend(eff.coverage)
        findings.extend(eff.findings)
    expected_ir = eff.decision_list if eff else None

    spec_low = lower_spec(bundle.spec) if bundle.spec else None
    wf_low = lower_workflow(bundle.workflow) if bundle.workflow else None
    code_lows = [(snippet, lower_code(snippet)) for snippet in bundle.code]

    artifacts: list[tuple[str, Any, list[CoverageEntry]]] = []
    if spec_low is None:
        coverage_entries.append(CoverageEntry(item="spec", kind="spec", status="not_reviewed",
            codes=["U-MISSING-ARTIFACT"], reason="structured specification is missing"))
    else:
        coverage_entries.extend(spec_low.coverage)
        artifacts.append(("spec", spec_low.ir, spec_low.coverage))
    if wf_low is None:
        coverage_entries.append(CoverageEntry(item="workflow", kind="workflow", status="not_reviewed",
            codes=["U-MISSING-ARTIFACT"], reason="workflow graph is missing"))
    else:
        coverage_entries.extend(wf_low.coverage)
        artifacts.append(("workflow", wf_low.ir, wf_low.coverage))
    if not code_lows:
        coverage_entries.append(CoverageEntry(item="code", kind="code", status="not_reviewed",
            codes=["U-MISSING-ARTIFACT"], reason="no linked Python snippets were supplied"))
    for snippet, low in code_lows:
        coverage_entries.extend(low.coverage)
        artifacts.append((f"code:{snippet.id}", low.ir, low.coverage))

    # Bundle-level structural/load issues are always visible.
    for issue in bundle.load_issues:
        coverage_entries.append(CoverageEntry(item=issue.evidence.location if issue.evidence else "bundle",
            kind="requirement", status="not_reviewed", codes=[issue.code], reason=issue.message,
            evidence=issue.evidence))

    irs = [expected_ir] + [ir for _, ir, _ in artifacts]
    tickets = ticket_domain([x for x in irs if x is not None])
    if expected_ir is None:
        checks.append(CheckResult(name="effective_requirements", required=True, status="error", mode="not_executed",
                                  detail="effective requirements could not be constructed"))
    else:
        checks.append(CheckResult(name="effective_requirements", required=True,
            status="passed" if not _coverage_unknown(expected_ir, coverage_entries) else "skipped",
            mode="static", detail="effective requirement decision list constructed; unknown coverage remains visible"))

    waits = sorted({ticket.wait_minutes for ticket in tickets})
    for name, ir, cov in artifacts:
        if expected_ir is None or ir is None:
            checks.append(CheckResult(name=f"semantic:{name}", required=True, status="skipped", mode="not_executed",
                detail="comparison skipped because one side could not be lowered"))
            continue
        diff = _first_difference(expected_ir, ir, tickets)
        if diff:
            ticket, expected, actual = diff
            source_hint = defect_hint = None
            if expected.by == expected_ir.default_evidence:
                source_hint = _threshold_location(expected_ir, ir, expected, actual, ticket, waits)
            if isinstance(ir, DecisionList) and actual.by == ir.default_evidence:
                defect_hint = _threshold_location(ir, expected_ir, actual, expected, ticket, waits)
            findings.append(_finding(bundle.id, name, ticket, expected, actual, source_hint, defect_hint))
            checks.append(CheckResult(name=f"semantic:{name}", required=True, status="failed", mode="exhaustive",
                detail="a counterexample was found in the supported boundary-partitioned domain", tickets_checked=len(tickets)))
        else:
            unknown_seen = any(ir.evaluate(t).route is UNKNOWN for t in tickets) or any(expected_ir.evaluate(t).route is UNKNOWN for t in tickets)
            checks.append(CheckResult(name=f"semantic:{name}", required=True,
                status="skipped" if unknown_seen or _coverage_unknown(ir, cov) else "passed", mode="exhaustive",
                detail=("no mismatch among reviewed representatives; unknown regions remain unreviewed" if unknown_seen
                        else "equivalent on every representative of the supported condition partition"),
                tickets_checked=len(tickets)))

    # Any non-behavioural issue discovered by Step 5 remains a warning/blocker
    # as supplied by effective.py; deterministic semantic blockers take priority.
    coverage = Coverage(entries=coverage_entries)
    status = decide_status(findings, coverage, checks)
    unreviewed = coverage.unreviewed()
    blockers = [f for f in findings if f.severity == "blocker" and f.source == "deterministic"]
    if status == "CHANGES_REQUIRED":
        summary = f"Found {len(blockers)} evidenced deterministic blocker(s); {len(unreviewed)} coverage item(s) remain unreviewed."
    elif status == "INCOMPLETE":
        summary = f"No deterministic blocker was established, but {len(unreviewed)} coverage item(s) or required checks remain unreviewed."
    else:
        summary = "All required artifacts were reviewed and no semantic mismatch was found within the supported domain."

    return Report(bundle_id=bundle.id, status=status, summary=summary, findings=findings,
        coverage=coverage, check_results=checks,
        provenance=Provenance(reviewer_version="step6-semantic-v1"))
