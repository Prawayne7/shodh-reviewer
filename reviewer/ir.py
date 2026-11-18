"""Internal rule representation shared by every parser (Stage 1 / Step 5).

Every artifact is lowered to one of three evaluable shapes:

* ``DecisionList``  requirements (effective) and spec: ordered rules + exceptions + default
* ``WorkflowIR``    workflow graph of decision and terminal nodes
* ``CodeIR``        restricted ``if``/``return`` tree parsed from Python with ``ast``

All three expose ``evaluate(ticket) -> Decision``. Nothing here executes source
code: code is walked as a data structure.

Wait thresholds are kept as exact ``Fraction`` minutes; comparing an integer
ticket wait with an exact fraction is the same as the integer-cut rewrite in
docs/supported_language.md section 8, without any rewriting to get wrong.

``UNKNOWN`` is the result for a ticket that reaches content that could not be
reviewed (unsupported, ambiguous or conflicted). It is never a destination:
comparison code must exclude such tickets (docs section 3.2).
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable, Iterator, Union

from . import schemas as S
from .schemas import CoverageEntry, EvidenceLocation, Ticket

SIZE_LIMITS = {"rules": 50, "atoms": 12, "nodes": 200, "code_lines": 400}

# Issue/coverage codes that mean "outside the supported language". Everything
# else is "ambiguous" (supported language, but the meaning is not determined).
UNSUPPORTED_CODES = {
    "U-PROSE", "U-INSTRUCTION", "U-APPROVAL-PROSE", "U-UNKNOWN-FIELD", "U-UNKNOWN-OP",
    "U-PY-CONSTRUCT", "U-PY-SYNTAX", "U-APPROVAL-STATUS", "U-APPROVAL-KIND",
}


def coverage_status(codes: list[str]) -> str:
    if "U-SIZE-LIMIT" in codes:
        return "not_reviewed"
    return "unsupported" if any(c in UNSUPPORTED_CODES for c in codes) else "ambiguous"


class _Unknown:
    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN = _Unknown()
Route = Union[str, None, _Unknown]  # destination | None = "no route" | UNKNOWN


@dataclass(frozen=True)
class Decision:
    route: Route
    by: EvidenceLocation | None  # the deciding element (docs section 10.2)


# ---- conditions ------------------------------------------------------------------
@dataclass(frozen=True)
class Atom:
    field: str  # category | urgent | tier | wait
    op: str  # category: eq ne is_missing is_present; urgent/tier: eq ne; wait: eq ne lt le gt ge
    value: Any = None  # str | bool | Fraction (minutes) | None


@dataclass(frozen=True)
class And:
    items: tuple


@dataclass(frozen=True)
class Or:
    items: tuple


@dataclass(frozen=True)
class Not:
    item: Any


@dataclass(frozen=True)
class Const:
    value: bool


_CMP: dict[str, Callable[[Any, Any], bool]] = {
    "eq": operator.eq, "ne": operator.ne, "lt": operator.lt,
    "le": operator.le, "gt": operator.gt, "ge": operator.ge,
}


def holds(c: Any, t: Ticket) -> bool:
    if isinstance(c, Atom):
        f, op = c.field, c.op
        if f == "category":
            x = t.category
            if op == "is_missing":
                return x is None
            if op == "is_present":
                return x is not None
            return _CMP[op](x, c.value)  # a missing category: eq false, ne true
        if f == "urgent":
            return _CMP[op](t.urgent, c.value)
        if f == "tier":
            return _CMP[op](t.tier, c.value)
        if f == "wait":
            return _CMP[op](Fraction(t.wait_minutes), c.value)
        raise ValueError(f"unknown field {f}")
    if isinstance(c, And):
        return all(holds(x, t) for x in c.items)
    if isinstance(c, Or):
        return any(holds(x, t) for x in c.items)
    if isinstance(c, Not):
        return not holds(c.item, t)
    if isinstance(c, Const):
        return c.value
    raise TypeError(f"not a condition: {c!r}")


def iter_atoms(c: Any) -> Iterator[Atom]:
    if isinstance(c, Atom):
        yield c
    elif isinstance(c, (And, Or)):
        for x in c.items:
            yield from iter_atoms(x)
    elif isinstance(c, Not):
        yield from iter_atoms(c.item)


def map_atoms(c: Any, fn: Callable[[Atom], Any]) -> Any:
    if isinstance(c, Atom):
        return fn(c)
    if isinstance(c, (And, Or)):
        return type(c)(tuple(map_atoms(x, fn) for x in c.items))
    if isinstance(c, Not):
        return Not(map_atoms(c.item, fn))
    return c


def wait_thresholds(c: Any) -> list[Fraction]:
    return [a.value for a in iter_atoms(c) if a.field == "wait"]


# ---- lowering a schema condition (spec / workflow) ------------------------------
def _minutes(value: Any, unit: str | None) -> Fraction:
    return Fraction(str(value)) * (60 if unit == "hours" else 1)


def lower_schema_cond(cond: Any, declared_wait_unit: str | None) -> tuple[Any | None, list[tuple[str, str]]]:
    """Convert a schema condition. Returns (IR or None, [(issue code, message)]).

    The IR is None whenever any leaf has a problem: the element is then unknown.
    """
    problems: list[tuple[str, str]] = []
    leaves = list(S.iter_leaves(cond))
    if len(leaves) > SIZE_LIMITS["atoms"]:
        return None, [("U-SIZE-LIMIT", f"condition has {len(leaves)} atoms; limit is {SIZE_LIMITS['atoms']}")]
    for leaf in leaves:
        problems += S.leaf_issues(leaf, declared_wait_unit)
    if problems:
        return None, problems

    def conv(c: Any) -> Any:
        if isinstance(c, S.Leaf):
            if c.field == "wait":
                return Atom("wait", c.op, _minutes(c.value, c.unit or declared_wait_unit))
            if c.field == "category" and isinstance(c.value, str):
                return Atom("category", c.op, c.value.strip())
            return Atom(c.field, c.op, c.value)
        if isinstance(c, S.AllOf):
            return And(tuple(conv(x) for x in c.all_))
        if isinstance(c, S.AnyOf):
            return Or(tuple(conv(x) for x in c.any_))
        if isinstance(c, S.Not):
            return Not(conv(c.not_))
        raise TypeError(c)

    return conv(cond), []


# ---- decision list (requirements, spec) --------------------------------------------
@dataclass
class ExceptionIR:
    id: str
    evidence: EvidenceLocation
    cond: Any | None
    skip: bool = False
    route: str | None = None
    status: str = "ok"  # ok | unknown


@dataclass
class RuleIR:
    id: str
    evidence: EvidenceLocation
    cond: Any | None
    route: str | None
    exceptions: list[ExceptionIR] = field(default_factory=list)
    status: str = "ok"  # ok | unknown


@dataclass
class DecisionList:
    rules: list[RuleIR]
    default: str | None
    default_evidence: EvidenceLocation
    default_status: str = "ok"  # ok | unknown
    no_default: Route = None  # what a ticket gets when there is no usable default

    def evaluate(self, t: Ticket) -> Decision:
        for r in self.rules:
            if r.status != "ok":
                return Decision(UNKNOWN, r.evidence)
            if not holds(r.cond, t):
                continue
            skipped = False
            for e in r.exceptions:
                if e.status != "ok":
                    return Decision(UNKNOWN, e.evidence)
                if holds(e.cond, t):
                    if e.skip:
                        skipped = True
                        break
                    return Decision(e.route, e.evidence)
            if not skipped:
                return Decision(r.route, r.evidence)
        if self.default_status != "ok":
            return Decision(UNKNOWN, self.default_evidence)
        if self.default is None:
            return Decision(self.no_default, self.default_evidence)
        return Decision(self.default, self.default_evidence)

    def conditions(self) -> Iterator[Any]:
        for r in self.rules:
            if r.cond is not None:
                yield r.cond
            for e in r.exceptions:
                if e.cond is not None:
                    yield e.cond


# ---- workflow graph ------------------------------------------------------------------
@dataclass
class BranchIR:
    order: int
    evidence: EvidenceLocation
    to: str
    cond: Any | None = None
    is_else: bool = False
    status: str = "ok"  # ok | unknown


@dataclass
class NodeIR:
    id: str
    kind: str  # decision | terminal
    evidence: EvidenceLocation
    branches: list[BranchIR] = field(default_factory=list)  # sorted by order
    destination: str | None = None


@dataclass
class WorkflowIR:
    start: str
    nodes: dict[str, NodeIR]

    def evaluate(self, t: Ticket) -> Decision:
        cur = self.nodes[self.start]
        by = cur.evidence
        for _ in range(len(self.nodes) + 1):
            if cur.kind == "terminal":
                return Decision(cur.destination, by)
            for b in cur.branches:
                if b.status != "ok":
                    return Decision(UNKNOWN, b.evidence)
                if b.is_else or holds(b.cond, t):
                    by = b.evidence
                    cur = self.nodes[b.to]
                    break
            else:  # no branch applies: the workflow has no route (a missing branch)
                return Decision(None, cur.evidence)
        raise RuntimeError("workflow cycle (should have been rejected at lowering)")

    def conditions(self) -> Iterator[Any]:
        for n in self.nodes.values():
            for b in n.branches:
                if b.cond is not None:
                    yield b.cond


# ---- code tree (never executed) -----------------------------------------------------
@dataclass
class Return:
    dest: str
    start: int
    end: int


@dataclass
class If:
    cond: Any
    body: list
    orelse: list
    line: int


@dataclass
class CodeIR:
    artifact: str  # code:<file>
    version: str
    function_lines: tuple[int, int]
    body: list

    def _loc(self, a: int, b: int) -> EvidenceLocation:
        return EvidenceLocation(artifact=self.artifact, version=self.version, location=f"L{a}-L{b}")

    def _run(self, stmts: list, t: Ticket, gov: int | None) -> Decision | None:
        for s in stmts:
            if isinstance(s, Return):
                return Decision(s.dest, self._loc(gov if gov is not None else s.start, s.end))
            hit = self._run(s.body if holds(s.cond, t) else s.orelse, t, s.line)
            if hit is not None:
                return hit
        return None

    def evaluate(self, t: Ticket) -> Decision:
        hit = self._run(self.body, t, None)
        return hit if hit is not None else Decision(None, self._loc(*self.function_lines))

    def conditions(self) -> Iterator[Any]:
        def walk(stmts: list) -> Iterator[Any]:
            for s in stmts:
                if isinstance(s, If):
                    yield s.cond
                    yield from walk(s.body)
                    yield from walk(s.orelse)

        yield from walk(self.body)


# ---- result of lowering one artifact -----------------------------------------------------
@dataclass
class Lowered:
    ir: Any | None  # None = the artifact could not be reviewed at all
    coverage: list[CoverageEntry] = field(default_factory=list)
    issues: list[S.Issue] = field(default_factory=list)
