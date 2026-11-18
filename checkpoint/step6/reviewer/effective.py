"""Effective requirements = parsed requirements + applicable approved changes (Step 5).

Implements docs/supported_language.md section 9:

* only top-level records with status ``approved`` change anything; prose claims never do
* a record applies to the requirement it targets, in the source version it names
  (a version mismatch is *inapplicable*: listed, no effect)
* applicable records for one target and attribute are applied in ascending
  ``sequence``; a missing sequence is ambiguous
* contradictions are detected, never resolved: same sequence with different
  changes, a broken ``from``/``to`` chain, or one record id with two different
  contents. The target becomes ``conflicted`` and drops out of comparison.
* stale (``from`` matches nothing), dangling (unknown target) and unsupported
  records are reported and leave the target unreviewed where they touch it

The result is a ``DecisionList`` in which every unreviewed requirement is an
*unknown* element: tickets that reach it evaluate to ``UNKNOWN`` (section 3.2).
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Any

from .ir import (
    UNKNOWN, Atom, Decision, DecisionList, ExceptionIR, RuleIR, coverage_status, map_atoms,
)
from .parse_requirements import ParsedRequirements, ReqEntry, parse_requirements
from .schemas import (
    APPROVAL_STATUSES, ApprovalRecord, Approvals, Bundle, CoverageEntry, EvidenceItem,
    EvidenceLocation, Finding, Ticket,
)

SUPPORTED_KINDS = {"replace_threshold", "replace_destination"}


@dataclass
class AppliedChange:
    record_id: str
    target: str
    attribute: str
    before: Any
    after: Any


@dataclass
class EffectiveRequirements:
    version: str | None
    entries: list[ReqEntry]
    decision_list: DecisionList | None
    coverage: list[CoverageEntry] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    applied: list[AppliedChange] = field(default_factory=list)

    def evaluate(self, t: Ticket) -> Decision:
        if self.decision_list is None:
            return Decision(UNKNOWN, None)
        return self.decision_list.evaluate(t)


# ---- helpers ------------------------------------------------------------------------
def _qty(v: Any) -> Fraction | None:
    """A {value, unit} quantity in exact minutes, or None if malformed."""
    try:
        if not isinstance(v, dict) or isinstance(v.get("value"), bool):
            return None
        unit = v.get("unit")
        if unit not in ("minutes", "hours"):
            return None
        n = Fraction(str(v["value"]))
        return n * 60 if unit == "hours" else n
    except (KeyError, ValueError, ZeroDivisionError):
        return None


def _change_values(r: ApprovalRecord) -> tuple[str, Any, Any] | None:
    """(attribute, from, to) for a well-formed supported change, else None."""
    c = r.change
    if c.kind == "replace_threshold" and c.field == "wait":
        a, b = _qty(c.from_), _qty(c.to)
        return ("wait", a, b) if a is not None and b is not None else None
    if c.kind == "replace_destination" and isinstance(c.from_, str) and isinstance(c.to, str):
        return "route", c.from_, c.to
    return None


def _signature(r: ApprovalRecord) -> str:
    # free text (`note`) is not content; evidence/version are derived
    d = r.model_dump(mode="json", by_alias=True, exclude={"evidence", "version", "note"})
    return json.dumps(d, sort_keys=True)


def _app_cov(r: ApprovalRecord, status: str, codes: list[str], reason: str) -> CoverageEntry:
    return CoverageEntry(item=r.id, kind="approval", status=status, codes=codes, reason=reason, evidence=r.evidence)


def entry_coverage(e: ReqEntry) -> CoverageEntry | None:
    codes = sorted(set(e.codes) | set(e.extra_codes))
    if e.status == "ok" and not e.extra_codes:
        return None
    status = "conflicted" if e.status == "conflicted" else coverage_status(codes)
    return CoverageEntry(item=e.id, kind="requirement", status=status, codes=codes,
                         reason="; ".join(dict.fromkeys(e.reasons)), evidence=e.evidence)


def _conflict_finding(target: str, records: list[ApprovalRecord], reason: str, approvals_version: str) -> Finding:
    first = records[0]
    ids = ", ".join(r.id for r in records)
    return Finding(
        id=f"approval_conflict:{target}:{'+'.join(sorted(r.id for r in records))}",
        kind="approval_conflict", severity="warning", source="deterministic", rule_ref=target,
        artifact="approvals", version=approvals_version, location=first.id,
        summary=f"Approval records {ids} contradict each other for {target} ({reason}); none was applied.",
        evidence=[EvidenceItem(location=r.evidence, excerpt=f"{r.id}: {r.change.kind} {r.status}") for r in records],
        correction=f"Decide which change to {target} is authorised (keep one record, revoke or supersede the others). "
                   "The reviewer does not choose between contradictory approvals.",
    )


# ---- decision list from entries -----------------------------------------------------------
def _decision_list(entries: list[ReqEntry], version: str) -> DecisionList:
    rule_ids = {e.id for e in entries if e.kind == "rule"}
    exceptions: dict[str, list[ReqEntry]] = defaultdict(list)
    barriers: list[ReqEntry] = []
    for e in entries:
        if e.kind != "exception":
            continue
        (exceptions[e.target] if e.target in rule_ids else barriers).append(e)

    rows: list[tuple[int, RuleIR]] = []
    for e in entries:
        if e.kind not in ("rule", "unparsed"):
            continue
        ok = e.kind == "rule" and e.status == "ok"
        rule = RuleIR(id=e.id, evidence=e.evidence, cond=e.cond if ok else None, route=e.route if ok else None,
                      status="ok" if ok else "unknown")
        for x in sorted(exceptions.get(e.id, []), key=lambda x: x.order):
            xok = x.status == "ok"
            rule.exceptions.append(ExceptionIR(id=x.id, evidence=x.evidence, cond=x.cond if xok else None,
                                               skip=x.skip, route=x.route, status="ok" if xok else "unknown"))
        rows.append((e.order, rule))
    for x in barriers:  # an exception we cannot attach: conservative barrier at its own position
        rows.append((x.order, RuleIR(id=x.id, evidence=x.evidence, cond=None, route=None, status="unknown")))
    rows.sort(key=lambda r: r[0])

    defaults = [e for e in entries if e.kind == "default"]
    dev = EvidenceLocation(artifact="requirements", version=version,
                           location=defaults[0].id if len(defaults) == 1 else "default")
    if len(defaults) == 1 and defaults[0].status == "ok":
        return DecisionList([r for _, r in rows], defaults[0].route, defaults[0].evidence, "ok", UNKNOWN)
    return DecisionList([r for _, r in rows], None, dev, "unknown", UNKNOWN)


# ---- main entry points ------------------------------------------------------------------------
def build_effective(parsed: ParsedRequirements, approvals: Approvals) -> EffectiveRequirements:
    if not parsed.usable:
        return EffectiveRequirements(parsed.version, parsed.entries, None, list(parsed.global_coverage))

    entries = [replace(e, codes=list(e.codes), reasons=list(e.reasons), extra_codes=list(e.extra_codes)) for e in parsed.entries]
    by_id = {e.id: e for e in entries}
    version = parsed.version
    appr_cov: list[CoverageEntry] = []
    findings: list[Finding] = []
    applied: list[AppliedChange] = []
    seen_conflicts: set[frozenset] = set()

    def conflict(target: str, records: list[ApprovalRecord], reason: str) -> None:
        key = frozenset(r.id for r in records) | {target}
        if key in seen_conflicts:
            return
        seen_conflicts.add(key)
        if target in by_id:
            by_id[target].taint("conflicted", "approval_conflict", f"contradictory approvals ({', '.join(r.id for r in records)}): {reason}")
        for r in records:
            appr_cov.append(_app_cov(r, "conflicted", ["approval_conflict"], reason))
        findings.append(_conflict_finding(target, records, reason, approvals.version))

    # 1. one record id with two different contents
    by_rid: dict[str, list[ApprovalRecord]] = defaultdict(list)
    for r in approvals.records:
        by_rid[r.id].append(r)
    records: list[ApprovalRecord] = []
    for rid, rs in by_rid.items():
        if len({_signature(x) for x in rs}) > 1:
            for target in dict.fromkeys(x.target for x in rs):
                conflict(target, [x for x in rs if x.target == target] or rs, f"record id {rid} appears with different content")
        else:
            records.append(rs[0])

    # 2. filter: status, revision scope, target, kind, sequence
    live: list[ApprovalRecord] = []
    for r in records:
        target = by_id.get(r.target)
        if r.status not in APPROVAL_STATUSES:
            appr_cov.append(_app_cov(r, "unsupported", ["U-APPROVAL-STATUS"], f"unknown status {r.status!r}"))
            if target:
                target.taint("unsupported", "U-APPROVAL-STATUS", f"approval {r.id} has unknown status {r.status!r}")
            continue
        if r.status != "approved":
            appr_cov.append(_app_cov(r, "ignored", [], f"status {r.status}: an unapproved record cannot change a requirement"))
            continue
        if r.applies_to_source_version != version:
            appr_cov.append(_app_cov(r, "inapplicable", [], f"applies to {r.applies_to_source_version}, requirements are {version}"))
            continue
        if target is None:
            appr_cov.append(_app_cov(r, "ambiguous", ["dangling_approval"], f"no requirement {r.target!r}"))
            continue
        if _change_values(r) is None:
            appr_cov.append(_app_cov(r, "unsupported", ["U-APPROVAL-KIND"], f"unsupported or malformed change {r.change.kind!r}"))
            target.taint("unsupported", "U-APPROVAL-KIND", f"approval {r.id} has an unsupported change")
            continue
        if r.sequence is None:
            appr_cov.append(_app_cov(r, "ambiguous", ["missing_sequence"], "no sequence: the order of changes is undefined"))
            target.taint("ambiguous", "missing_sequence", f"approval {r.id} has no sequence")
            continue
        live.append(r)

    # 3. per (target, attribute): same-sequence and chain checks, then apply
    groups: dict[tuple[str, str], list[ApprovalRecord]] = defaultdict(list)
    for r in live:
        groups[(r.target, _change_values(r)[0])].append(r)
    for (tid, attr), grp in groups.items():
        entry = by_id[tid]
        if entry.status in ("unsupported", "conflicted") or entry.kind == "unparsed":
            for r in grp:
                appr_cov.append(_app_cov(r, "ambiguous", ["target_unreviewable"], f"{tid} could not be parsed, so the change cannot be applied"))
            continue
        by_seq: dict[int, list[ApprovalRecord]] = defaultdict(list)
        for r in grp:
            by_seq[r.sequence].append(r)
        chain: list[ApprovalRecord] = []
        bad = False
        for seq in sorted(by_seq):
            rs = by_seq[seq]
            if len({(_change_values(x)[1], _change_values(x)[2]) for x in rs}) > 1:
                conflict(tid, rs, f"same sequence {seq}, different changes")
                bad = True
                break
            chain.append(rs[0])
        if bad:
            continue
        for prev, cur in zip(chain, chain[1:]):
            if _change_values(cur)[1] != _change_values(prev)[2]:
                conflict(tid, [prev, cur], "broken chain: 'from' does not equal the previous 'to'")
                bad = True
                break
        if bad:
            continue
        # apply on a copy; commit only if the first change matches something
        cond, route = entry.cond, entry.route
        trail: list[AppliedChange] = []
        stale = False
        for r in chain:
            _, frm, to = _change_values(r)
            if attr == "wait":
                hits = 0

                def swap(a: Atom, frm=frm, to=to) -> Any:
                    nonlocal hits
                    if a.field == "wait" and a.value == frm:
                        hits += 1
                        return Atom("wait", a.op, to)
                    return a

                new = map_atoms(cond, swap) if cond is not None else None
                if not hits:
                    stale = True
                    appr_cov.append(_app_cov(r, "ambiguous", ["stale_approval"], f"no wait threshold equal to {frm} minutes in {tid}"))
                    entry.taint("ambiguous", "stale_approval", f"approval {r.id} does not match the current value")
                    break
                cond = new
            else:
                if route != frm:
                    stale = True
                    appr_cov.append(_app_cov(r, "ambiguous", ["stale_approval"], f"destination of {tid} is not {frm!r}"))
                    entry.taint("ambiguous", "stale_approval", f"approval {r.id} does not match the current value")
                    break
                route = to
            trail.append(AppliedChange(r.id, tid, attr, frm, to))
        if not stale:
            entry.cond, entry.route = cond, route
            applied += trail

    # 4. coverage and decision list
    cov = [c for c in (entry_coverage(e) for e in entries) if c] + list(parsed.global_coverage) + appr_cov
    return EffectiveRequirements(version, entries, _decision_list(entries, version), cov, findings, applied)


def effective_from_bundle(bundle: Bundle) -> EffectiveRequirements:
    if bundle.requirements is None:
        cov = [CoverageEntry(item="requirements", kind="requirement", status="not_reviewed",
                             codes=["U-MISSING-ARTIFACT"], reason="no usable requirements artifact")]
        return EffectiveRequirements(None, [], None, cov)
    vocab = list(bundle.spec.destinations) if bundle.spec else None
    return build_effective(parse_requirements(bundle.requirements, vocab), bundle.approvals)
