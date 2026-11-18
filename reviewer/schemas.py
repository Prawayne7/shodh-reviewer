"""Pydantic contracts for the workflow reviewer (Stage 1 / Step 3).

Design rules (full text in docs/supported_language.md):

* Structural errors (missing key, wrong type, unknown key) raise
  ``ValidationError``; the loader turns them into a visible ``U-SCHEMA`` issue
  for that artifact, so nothing silently disappears from coverage.
* Semantic problems (unknown field, ambiguous unit, duplicate ``order`` ...)
  never raise. They are returned by ``structural_issues()`` so they stay visible.
* IDs and ``refs`` are hints only. Evidence locations are built from ids and
  ``order`` values, never from array positions, so reordering serialized records
  cannot change a citation.
* Expected labels have no field here. ``extra="forbid"`` makes a label leaked
  into a reviewer input a schema error.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Iterator, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    Tag,
    field_validator,
    model_validator,
)

REPORT_SCHEMA_VERSION = "report-v1"

FIELDS = ("category", "urgent", "tier", "wait")
OPS = ("eq", "ne", "lt", "le", "gt", "ge", "is_missing", "is_present")
TIERS = ("standard", "vip")
WAIT_UNITS = ("minutes", "hours")
APPROVAL_STATUSES = ("approved", "pending", "rejected", "revoked")

_FIELD_OPS = {
    "category": {"eq", "ne", "is_missing", "is_present"},
    "urgent": {"eq", "ne"},
    "tier": {"eq", "ne"},
    "wait": {"eq", "ne", "lt", "le", "gt", "ge"},
}
_PRESENCE_OPS = {"is_missing", "is_present"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------
class EvidenceLocation(_Model):
    """artifact@version#location, e.g. ``spec@spec-v3#S2.E1``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact: str
    version: str
    location: str

    def cite(self) -> str:
        return f"{self.artifact}@{self.version}#{self.location}"


class EvidenceItem(_Model):
    location: EvidenceLocation
    excerpt: str | None = None


class Issue(_Model):
    """A visible problem: unsupported, ambiguous or malformed content."""

    code: str = Field(pattern=r"^U-[A-Z0-9-]+$")
    message: str
    evidence: EvidenceLocation | None = None


# --------------------------------------------------------------------------
# Ticket domain
# --------------------------------------------------------------------------
def normalise_category(value: Any) -> str | None:
    """Trim; empty or whitespace-only means missing (same as null)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("category must be a string or null")
    trimmed = value.strip()
    return trimmed or None


class Ticket(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str | None
    urgent: StrictBool
    tier: Literal["standard", "vip"]
    wait_minutes: StrictInt = Field(ge=0)

    @field_validator("category", mode="before")
    @classmethod
    def _normalise(cls, v: Any) -> str | None:
        return normalise_category(v)


# --------------------------------------------------------------------------
# Condition AST: leaf | all | any | not  (shared by spec and workflow)
# --------------------------------------------------------------------------
Scalar = Union[StrictBool, StrictInt, StrictFloat, StrictStr, None]


class Leaf(_Model):
    field: str
    op: str
    value: Scalar = None
    unit: str | None = None


class AllOf(_Model):
    all_: list[Cond] = Field(alias="all", min_length=1)


class AnyOf(_Model):
    any_: list[Cond] = Field(alias="any", min_length=1)


class Not(_Model):
    not_: Cond = Field(alias="not")


def _cond_tag(v: Any) -> str | None:
    if isinstance(v, dict):
        for key in ("all", "any", "not"):
            if key in v:
                return key
        return "leaf"
    for tag, cls in (("all", AllOf), ("any", AnyOf), ("not", Not), ("leaf", Leaf)):
        if isinstance(v, cls):
            return tag
    return None


Cond = Annotated[
    Union[
        Annotated[Leaf, Tag("leaf")],
        Annotated[AllOf, Tag("all")],
        Annotated[AnyOf, Tag("any")],
        Annotated[Not, Tag("not")],
    ],
    Discriminator(_cond_tag),
]
for _m in (AllOf, AnyOf, Not):
    _m.model_rebuild()


def iter_leaves(cond: Any) -> Iterator[Leaf]:
    if isinstance(cond, Leaf):
        yield cond
    elif isinstance(cond, AllOf):
        for c in cond.all_:
            yield from iter_leaves(c)
    elif isinstance(cond, AnyOf):
        for c in cond.any_:
            yield from iter_leaves(c)
    elif isinstance(cond, Not):
        yield from iter_leaves(cond.not_)


def leaf_issues(leaf: Leaf, declared_wait_unit: str | None) -> list[tuple[str, str]]:
    """Semantic problems of one leaf as (issue code, message). Never raises."""
    if leaf.field not in FIELDS:
        return [("U-UNKNOWN-FIELD", f"unknown field {leaf.field!r}")]
    if leaf.op not in OPS:
        return [("U-UNKNOWN-OP", f"unknown operator {leaf.op!r}")]
    out: list[tuple[str, str]] = []
    if leaf.op not in _FIELD_OPS[leaf.field]:
        out.append(("U-LEAF-SHAPE", f"operator {leaf.op} is not valid for {leaf.field}"))
        return out
    v = leaf.value
    if leaf.op in _PRESENCE_OPS:
        if v is not None:
            out.append(("U-LEAF-SHAPE", f"{leaf.op} takes no value"))
    elif v is None:
        out.append(("U-LEAF-SHAPE", f"{leaf.op} needs a value"))
    elif leaf.field == "category":
        if not isinstance(v, str):
            out.append(("U-LEAF-SHAPE", "category value must be a string"))
        elif v.strip() == "":
            out.append(("U-EMPTY-LITERAL", "empty category literal: empty and missing are the same thing here"))
    elif leaf.field == "urgent":
        if not isinstance(v, bool):
            out.append(("U-LEAF-SHAPE", "urgent value must be true or false"))
    elif leaf.field == "tier":
        if v not in TIERS:
            out.append(("U-LEAF-SHAPE", f"tier value must be one of {list(TIERS)}"))
    elif leaf.field == "wait":
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            out.append(("U-LEAF-SHAPE", "wait value must be a non-negative number"))
    if leaf.field == "wait":
        unit = leaf.unit or declared_wait_unit
        if unit not in WAIT_UNITS:
            out.append(("U-AMBIG-UNIT", "wait needs a unit of minutes or hours (on the leaf or declared by the artifact)"))
    elif leaf.unit is not None:
        out.append(("U-LEAF-SHAPE", f"unit is only valid on wait, not {leaf.field}"))
    return out


def _cond_issues(cond: Any, declared: str | None, ev: EvidenceLocation | None) -> list[Issue]:
    return [
        Issue(code=code, message=msg, evidence=ev)
        for leaf in iter_leaves(cond)
        for code, msg in leaf_issues(leaf, declared)
    ]


def _duplicates(values: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    dups: list[Any] = []
    for v in values:
        if v in seen and v not in dups:
            dups.append(v)
        seen.add(v)
    return dups


# --------------------------------------------------------------------------
# Artifact elements. ``version`` and ``evidence`` are stamped by the container.
# ``note`` is free text: never parsed, never trusted (docs section 11).
# --------------------------------------------------------------------------
Order = Annotated[StrictInt, Field(ge=1)]


class Element(_Model):
    version: str | None = None
    evidence: EvidenceLocation | None = None
    note: str | None = None


def _stamp(el: Element, artifact: str, version: str, location: str) -> None:
    if el.version is None:
        el.version = version
    if el.evidence is None:
        el.evidence = EvidenceLocation(artifact=artifact, version=el.version, location=location)


def ordered(items: list[Any]) -> list[Any]:
    """Sort by the explicit ``order`` field (stable); array position is never semantic."""
    return sorted(items, key=lambda i: i.order)


# ---- requirements --------------------------------------------------------
class SourceRequirement(Element):
    id: str
    order: Order
    text: str
    line: StrictInt | None = None


class RequirementsDoc(Element):
    version: str  # type: ignore[assignment]
    requirements: list[SourceRequirement]

    @model_validator(mode="after")
    def _stamp_children(self) -> "RequirementsDoc":
        _stamp(self, "requirements", self.version, "document")
        for r in self.requirements:
            _stamp(r, "requirements", self.version, r.id)
        return self

    def issues(self) -> list[Issue]:
        out = [
            Issue(code="U-DUP-ID", message=f"duplicate requirement id {d}", evidence=self.evidence)
            for d in _duplicates([r.id for r in self.requirements])
        ]
        out += [
            Issue(code="U-ORDER-AMBIG", message=f"duplicate requirement order {d}", evidence=self.evidence)
            for d in _duplicates([r.order for r in self.requirements])
        ]
        return out


# ---- spec ----------------------------------------------------------------
class RouteAction(_Model):
    route: str


class SpecException(Element):
    id: str | None = None
    order: Order
    when: Cond
    action: Union[Literal["skip"], RouteAction]


class SpecRule(Element):
    id: str
    order: Order
    refs: list[str] = Field(default_factory=list)  # hints only
    when: Cond
    route: str
    exceptions: list[SpecException] = Field(default_factory=list)


class Spec(Element):
    version: str  # type: ignore[assignment]
    destinations: list[str] = Field(min_length=1)
    units: dict[str, str] = Field(default_factory=dict)
    rules: list[SpecRule]
    default: str | None = None

    @model_validator(mode="after")
    def _stamp_children(self) -> "Spec":
        _stamp(self, "spec", self.version, "document")
        for r in self.rules:
            _stamp(r, "spec", self.version, r.id)
            for e in r.exceptions:
                _stamp(e, "spec", self.version, e.id or f"{r.id}.E{e.order}")
        return self

    def issues(self) -> list[Issue]:
        out: list[Issue] = []
        declared = self.units.get("wait")
        vocab = set(self.destinations)
        for d in _duplicates([r.id for r in self.rules]):
            out.append(Issue(code="U-DUP-ID", message=f"duplicate spec rule id {d}", evidence=self.evidence))
        for d in _duplicates([r.order for r in self.rules]):
            out.append(Issue(code="U-ORDER-AMBIG", message=f"duplicate rule order {d}", evidence=self.evidence))
        for r in self.rules:
            out += _cond_issues(r.when, declared, r.evidence)
            if r.route not in vocab:
                out.append(Issue(code="U-DEST-UNKNOWN", message=f"{r.route!r} is not a declared destination", evidence=r.evidence))
            for d in _duplicates([e.order for e in r.exceptions]):
                out.append(Issue(code="U-ORDER-AMBIG", message=f"duplicate exception order {d}", evidence=r.evidence))
            for e in r.exceptions:
                out += _cond_issues(e.when, declared, e.evidence)
                if isinstance(e.action, RouteAction) and e.action.route not in vocab:
                    out.append(Issue(code="U-DEST-UNKNOWN", message=f"{e.action.route!r} is not a declared destination", evidence=e.evidence))
        if self.default is not None and self.default not in vocab:
            out.append(Issue(code="U-DEST-UNKNOWN", message=f"default {self.default!r} is not a declared destination", evidence=self.evidence))
        return out


# ---- workflow --------------------------------------------------------------
class Branch(Element):
    order: Order
    when: Cond | None = None
    to: str | None = None
    else_: str | None = Field(default=None, alias="else")

    @model_validator(mode="after")
    def _shape(self) -> "Branch":
        is_cond = self.when is not None and self.to is not None and self.else_ is None
        is_else = self.else_ is not None and self.when is None and self.to is None
        if not (is_cond or is_else):
            raise ValueError("a branch is either {when, to} or {else}")
        return self


class WorkflowNode(Element):
    id: str
    type: Literal["decision", "terminal"]
    refs: list[str] = Field(default_factory=list)  # hints only
    branches: list[Branch] = Field(default_factory=list)
    destination: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> "WorkflowNode":
        if self.type == "decision" and (not self.branches or self.destination is not None):
            raise ValueError("a decision node needs branches and no destination")
        if self.type == "terminal" and (self.destination is None or self.branches):
            raise ValueError("a terminal node needs a destination and no branches")
        return self


class Workflow(Element):
    version: str  # type: ignore[assignment]
    start: str
    units: dict[str, str] = Field(default_factory=dict)
    nodes: list[WorkflowNode]

    @model_validator(mode="after")
    def _stamp_children(self) -> "Workflow":
        _stamp(self, "workflow", self.version, "document")
        for n in self.nodes:
            _stamp(n, "workflow", self.version, n.id)
            for b in n.branches:
                _stamp(b, "workflow", n.version or self.version, f"{n.id}.b{b.order}")
        return self

    def issues(self) -> list[Issue]:
        """Cycle and reachability checks need a graph walk and live in parse_workflow."""
        out: list[Issue] = []
        ids = [n.id for n in self.nodes]
        node_ids = set(ids)
        declared = self.units.get("wait")
        for d in _duplicates(ids):
            out.append(Issue(code="U-DUP-ID", message=f"duplicate node id {d}", evidence=self.evidence))
        if self.start not in node_ids:
            out.append(Issue(code="U-WF-STRUCTURE", message=f"start node {self.start!r} does not exist", evidence=self.evidence))
        for n in self.nodes:
            if n.type != "decision":
                continue
            for d in _duplicates([b.order for b in n.branches]):
                out.append(Issue(code="U-ORDER-AMBIG", message=f"duplicate branch order {d} in node {n.id}", evidence=n.evidence))
            elses = [b for b in n.branches if b.else_ is not None]
            if len(elses) > 1:
                out.append(Issue(code="U-WF-STRUCTURE", message=f"node {n.id} has more than one else branch", evidence=n.evidence))
            elif elses and elses[0].order != max(b.order for b in n.branches):
                out.append(Issue(code="U-WF-STRUCTURE", message=f"else branch of node {n.id} is not last", evidence=elses[0].evidence))
            for b in n.branches:
                target = b.else_ if b.else_ is not None else b.to
                if target not in node_ids:
                    out.append(Issue(code="U-WF-STRUCTURE", message=f"branch target {target!r} does not exist", evidence=b.evidence))
                if b.when is not None:
                    out += _cond_issues(b.when, declared, b.evidence)
        return out


# ---- code ------------------------------------------------------------------
class CodeSnippet(Element):
    id: str
    file: str
    function: str = "route"
    version: str  # type: ignore[assignment]
    refs: list[str] = Field(default_factory=list)  # hints only
    source: str

    @property
    def line_count(self) -> int:
        return len(self.source.splitlines())

    @model_validator(mode="after")
    def _stamp_self(self) -> "CodeSnippet":
        if self.evidence is None:
            self.evidence = EvidenceLocation(
                artifact=f"code:{self.file}",
                version=self.version,
                location=f"L1-L{max(self.line_count, 1)}",
            )
        return self


# ---- approvals --------------------------------------------------------------
class ApprovalChange(_Model):
    kind: str
    field: str | None = None
    from_: Any = Field(default=None, alias="from")
    to: Any = None


class ApprovalRecord(Element):
    id: str
    status: str
    target: str
    applies_to_source_version: str
    sequence: StrictInt | None = None
    change: ApprovalChange
    approver: str | None = None
    approved_on: date | None = None


class Approvals(Element):
    version: str = "unversioned"  # type: ignore[assignment]
    records: list[ApprovalRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _stamp_children(self) -> "Approvals":
        for r in self.records:
            _stamp(r, "approvals", self.version, r.id)
        return self


# ---- bundle -----------------------------------------------------------------
class Bundle(_Model):
    """Everything the reviewer may read. No label, hint or expected-outcome field exists."""

    id: str
    requirements: RequirementsDoc | None = None
    spec: Spec | None = None
    workflow: Workflow | None = None
    code: list[CodeSnippet] = Field(default_factory=list)
    approvals: Approvals = Field(default_factory=Approvals)
    load_issues: list[Issue] = Field(default_factory=list)

    def structural_issues(self) -> list[Issue]:
        out = list(self.load_issues)
        if self.requirements:
            out += self.requirements.issues()
        if self.spec:
            out += self.spec.issues()
        if self.workflow:
            out += self.workflow.issues()
            if self.spec:
                vocab = set(self.spec.destinations)
                for n in self.workflow.nodes:
                    if n.type == "terminal" and n.destination not in vocab:
                        out.append(Issue(code="U-DEST-UNKNOWN", message=f"{n.destination!r} is not a declared destination", evidence=n.evidence))
        for d in _duplicates([c.id for c in self.code]):
            out.append(Issue(code="U-DUP-ID", message=f"duplicate code snippet id {d}", evidence=None))
        return out


# --------------------------------------------------------------------------
# Output: findings, coverage, checks, report
# --------------------------------------------------------------------------
Severity = Literal["blocker", "warning", "info"]

FindingKind = Literal[
    "omitted_requirement",
    "wrong_destination",
    "threshold_mismatch",
    "boundary_mismatch",
    "unit_mismatch",
    "precedence_mismatch",
    "missing_branch",
    "unapproved_change_implemented",
    "script_disagreement",
    "unclassified_mismatch",
    "destination_unknown",
    "structure_defect",
    "approval_conflict",
    "ref_hint_mismatch",
    "model_observation",
]
# Blockers of every other kind are behavioural and must carry a counterexample.
NON_BEHAVIOURAL_KINDS = {
    "destination_unknown",
    "structure_defect",
    "approval_conflict",
    "ref_hint_mismatch",
    "model_observation",
}


class Counterexample(_Model):
    ticket: Ticket
    expected_route: str
    actual_route: str | None  # None = the artifact has no route for this ticket
    artifact: str
    expected_by: EvidenceLocation
    actual_by: EvidenceLocation | None = None

    @model_validator(mode="after")
    def _differ(self) -> "Counterexample":
        if self.expected_route == self.actual_route:
            raise ValueError("a counterexample needs different expected and actual routes")
        return self


class Finding(_Model):
    id: str
    kind: FindingKind
    severity: Severity
    source: Literal["deterministic", "model"] = "deterministic"
    rule_ref: str | None = None
    artifact: str
    version: str
    location: str
    summary: str
    evidence: list[EvidenceItem] = Field(min_length=1)
    correction: str
    counterexample: Counterexample | None = None

    @model_validator(mode="after")
    def _rules(self) -> "Finding":
        if self.severity == "blocker":
            if self.source == "model":
                raise ValueError("a model finding can never be a blocker")
            if self.kind not in NON_BEHAVIOURAL_KINDS and self.counterexample is None:
                raise ValueError("a behavioural blocker needs a counterexample")
        return self


CoverageStatus = Literal[
    "reviewed",
    "unsupported",
    "ambiguous",
    "conflicted",
    "omitted",
    "not_reviewed",
    "inapplicable",
    "ignored",
]
# These statuses mean part of the effective requirements was not fully reviewed
# (or an omission was found), so the report cannot be PASS.
BLOCKS_PASS = {"unsupported", "ambiguous", "conflicted", "omitted", "not_reviewed"}


class CoverageEntry(_Model):
    item: str
    kind: Literal["requirement", "spec", "workflow", "code", "approval"]
    status: CoverageStatus
    codes: list[str] = Field(default_factory=list)
    reason: str = ""
    evidence: EvidenceLocation | None = None


class Coverage(_Model):
    entries: list[CoverageEntry] = Field(default_factory=list)

    def unreviewed(self) -> list[CoverageEntry]:
        return [e for e in self.entries if e.status in BLOCKS_PASS]


class CheckResult(_Model):
    name: str
    required: bool
    status: Literal["passed", "failed", "skipped", "error"]
    mode: Literal["exhaustive", "sampled", "static", "model", "not_executed"]
    detail: str = ""
    tickets_checked: int | None = None


class ModelCallRecord(_Model):
    provider: str
    model: str | None = None
    temperature: float | None = None
    prompt_version: str
    attempt: int = Field(ge=1)
    outcome: Literal[
        "ok", "timeout", "malformed_json", "truncated_json",
        "invalid_references", "conflicting_claims", "error",
    ]
    request_sha256: str
    response_sha256: str | None = None
    is_fake: bool


class Provenance(_Model):
    reviewer_version: str
    config_sha256: str | None = None
    model_calls: list[ModelCallRecord] = Field(default_factory=list)


Status = Literal["PASS", "CHANGES_REQUIRED", "INCOMPLETE"]


class Report(_Model):
    schema_version: Literal["report-v1"] = REPORT_SCHEMA_VERSION
    bundle_id: str
    status: Status
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    check_results: list[CheckResult] = Field(default_factory=list)
    provenance: Provenance

    @model_validator(mode="after")
    def _status_is_consistent(self) -> "Report":
        """The PDF's definitions of the three outcomes, enforced at construction."""
        blockers = [f for f in self.findings if f.severity == "blocker"]
        unreviewed = self.coverage.unreviewed()
        not_passed = [c for c in self.check_results if c.required and c.status != "passed"]
        if self.status == "CHANGES_REQUIRED" and not blockers:
            raise ValueError("CHANGES_REQUIRED needs at least one evidenced blocker")
        if self.status == "INCOMPLETE":
            if blockers:
                raise ValueError("INCOMPLETE is for reports with no established blocker")
            if not (unreviewed or not_passed):
                raise ValueError("INCOMPLETE needs unreviewed scope or a required check that did not pass")
        if self.status == "PASS":
            if blockers:
                raise ValueError("PASS is impossible with a blocker")
            if unreviewed:
                raise ValueError("PASS is impossible with unreviewed or omitted scope")
            if not_passed:
                raise ValueError("PASS is impossible while a required check has not passed")
        return self
