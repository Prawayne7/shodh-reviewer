"""Lower a ``Workflow`` to a ``WorkflowIR`` (Step 5).

Structural problems that make routing meaningless (duplicate ids, missing
start, dangling targets, duplicate branch orders, a misplaced ``else``, cycles)
leave the workflow unreviewed. An unreachable node does not change any route
but is still reported (docs section 6). A branch with a malformed condition is
*unknown*. A decision node with no matching branch yields ``None`` (no route).
"""
from __future__ import annotations

from collections import defaultdict

from .ir import (
    SIZE_LIMITS, BranchIR, Lowered, NodeIR, WorkflowIR, coverage_status, lower_schema_cond,
)
from .schemas import CoverageEntry, EvidenceLocation, Issue, Workflow, ordered

FATAL_CODES = {"U-DUP-ID", "U-ORDER-AMBIG", "U-WF-STRUCTURE"}


def _entry(item: str, codes: list[str], reasons: list[str], ev: EvidenceLocation | None) -> CoverageEntry:
    return CoverageEntry(item=item, kind="workflow", status=coverage_status(codes),
                         codes=sorted(set(codes)), reason="; ".join(reasons), evidence=ev)


def _find_cycle_or_unreachable(wf: Workflow) -> tuple[bool, set[str]]:
    """(has_cycle, reachable ids). Iterative DFS with colours; dangling targets are checked earlier."""
    succ: dict[str, list[str]] = {
        n.id: [b.else_ if b.else_ is not None else b.to for b in n.branches] for n in wf.nodes
    }
    colour: dict[str, int] = {}
    reachable: set[str] = set()
    stack: list[tuple[str, int]] = [(wf.start, 0)]
    while stack:
        node, idx = stack.pop()
        if idx == 0:
            if colour.get(node) == 1:
                return True, reachable
            if colour.get(node) == 2:
                continue
            colour[node] = 1
            reachable.add(node)
        targets = succ.get(node, [])
        if idx < len(targets):
            stack.append((node, idx + 1))
            nxt = targets[idx]
            if colour.get(nxt) == 1:
                return True, reachable
            if colour.get(nxt) != 2:
                stack.append((nxt, 0))
        else:
            colour[node] = 2
    return False, reachable


def lower_workflow(wf: Workflow) -> Lowered:
    issues = wf.issues()
    if len(wf.nodes) > SIZE_LIMITS["nodes"]:
        return Lowered(None, [_entry("workflow", ["U-SIZE-LIMIT"], [f"{len(wf.nodes)} nodes; limit {SIZE_LIMITS['nodes']}"], wf.evidence)], issues)
    fatal = [i for i in issues if i.code in FATAL_CODES]
    if fatal:
        return Lowered(None, [_entry("workflow", [i.code for i in fatal], [i.message for i in fatal], wf.evidence)], issues)
    cyclic, reachable = _find_cycle_or_unreachable(wf)
    if cyclic:
        issue = Issue(code="U-WF-STRUCTURE", message="the workflow graph contains a cycle", evidence=wf.evidence)
        return Lowered(None, [_entry("workflow", ["U-WF-STRUCTURE"], [issue.message], wf.evidence)], issues + [issue])

    by_loc: dict[str, list[Issue]] = defaultdict(list)
    for i in issues:
        by_loc[i.evidence.location if i.evidence else "document"].append(i)
    declared = wf.units.get("wait")
    coverage: list[CoverageEntry] = []
    nodes: dict[str, NodeIR] = {}
    for n in wf.nodes:
        node = NodeIR(id=n.id, kind=n.type, evidence=n.evidence, destination=n.destination)
        for b in ordered(n.branches):
            target = b.else_ if b.else_ is not None else b.to
            if b.when is None:
                node.branches.append(BranchIR(order=b.order, evidence=b.evidence, to=target, is_else=True))
                continue
            cond, probs = lower_schema_cond(b.when, declared)
            codes = [c for c, _ in probs]
            if codes:
                coverage.append(_entry(b.evidence.location, codes, [m for _, m in probs], b.evidence))
            node.branches.append(BranchIR(order=b.order, evidence=b.evidence, to=target, cond=cond,
                                          status="ok" if not codes else "unknown"))
        nodes[n.id] = node
    for n in wf.nodes:
        if n.id not in reachable:
            coverage.append(_entry(n.id, ["U-WF-STRUCTURE"], ["node is not reachable from the start node"], n.evidence))
    return Lowered(WorkflowIR(start=wf.start, nodes=nodes), coverage, issues)
