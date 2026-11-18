"""Parse source requirements (restricted English) into rule entries (Step 5).

Implements docs/supported_language.md section 4: sentence templates T1-T5, the
condition grammar with the atoms table, units, sentence segmentation and the
treatment of extra sentences (``U-PROSE`` / ``U-INSTRUCTION`` / ``U-APPROVAL-PROSE``).

Nothing is guessed: a sentence that matches no template, an atom with no unit,
or ``and``/``or`` mixed without parentheses leaves that entry unsupported or
ambiguous, visible in coverage. Approvals are applied later (effective.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from .ir import SIZE_LIMITS, And, Atom, Not, Or, coverage_status
from .schemas import CoverageEntry, EvidenceLocation, RequirementsDoc


class _Problem(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---- sentence templates ---------------------------------------------------------
_DEST = r"(?P<dest>[A-Za-z_][A-Za-z_ ]*?)"
_T4 = re.compile(r"exception to (?P<rid>[^\s:]+):\s*if (?P<cond>.+?),\s*do not apply (?P<rid2>.+?)\.?", re.I)
_T5 = re.compile(rf"exception to (?P<rid>[^\s:]+):\s*if (?P<cond>.+),\s*route to {_DEST}\.?", re.I)
_T3A = re.compile(rf"all remaining tickets\s*->\s*{_DEST}\.?", re.I)
_T3B = re.compile(rf"otherwise,\s*route to {_DEST}\.?", re.I)
_T2 = re.compile(rf"(?P<oth>otherwise,\s*)?if (?P<cond>.+),\s*route to {_DEST}\.?", re.I)
_T1 = re.compile(rf"(?P<oth>otherwise,\s*)?(?P<cond>.+?)\s*->\s*{_DEST}\.?", re.I)

_WS = re.compile(r"\s+")
_SPLIT = re.compile(r"(?<=\.)\s+")

_INSTRUCTION = re.compile(
    r"\b(ignore|disregard|override|bypass)\b.*\b(check|finding|rule|instruction|review|reviewer|polic)"
    r"|\b(mark|report|return|set|output|declare|give)\b.*\bpass\b"
    r"|\btreat\b.*\bas (approved|valid|passing)\b|\bdo not (report|flag|raise)\b|\byou (must|should)\b",
    re.I,
)
_APPROVAL = re.compile(r"\b(approved|approval|authori[sz]ed|signed off|sign-off|sanctioned)\b", re.I)


def _norm(s: str) -> str:
    return _WS.sub(" ", s.strip())


def _dest(s: str) -> str:
    return _norm(s).lower().replace(" ", "_")


# ---- condition grammar ----------------------------------------------------------
_NUM = r"(\d+(?:\.\d+)?)"
_UNIT = r"(?:\s*(minutes?|mins?|hours?|hrs?|h|days?|seconds?|secs?|weeks?)\b)?"
_CMP = r"(>=|<=|==|!=|>|<|=)"
_Q = r'"([^"]*)"'
_WAITWORD = r"(?:waiting|waited|wait)\s*"

_ATOMS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?:category is missing|missing category|no category)\b", re.I), "cat_missing"),
    (re.compile(r"(?:category is present|has a category)\b", re.I), "cat_present"),
    (re.compile(rf"category\s*(?:=|==|is|equals)\s*{_Q}", re.I), "cat_eq"),
    (re.compile(rf"category\s*(?:!=|is not)\s*{_Q}", re.I), "cat_ne"),
    (re.compile(rf'category is one of\s*{_Q}(?:\s*,\s*(?:or\s+)?{_Q})*', re.I), "cat_in"),
    (re.compile(r"urgent(?:\s+ticket)?(?:\s*=\s*(true|false))?\b", re.I), "urgent"),
    (re.compile(r"tier\s*(?:=|==|is)\s*(vip|standard)\b", re.I), "tier_eq"),
    (re.compile(r"tier\s*(?:!=|is not)\s*(vip|standard)\b", re.I), "tier_ne"),
    (re.compile(r"(vip|standard)\s+ticket\b", re.I), "tier_ticket"),
    (re.compile(rf"(?:{_WAITWORD})?{_CMP}\s*{_NUM}{_UNIT}", re.I), "wait_cmp"),
    (re.compile(rf"at least\s*{_NUM}{_UNIT}", re.I), "wait_ge"),
    (re.compile(rf"{_NUM}{_UNIT}\s+or more\b", re.I), "wait_ge"),
    (re.compile(rf"(?:more than|over)\s*{_NUM}{_UNIT}", re.I), "wait_gt"),
    (re.compile(rf"at most\s*{_NUM}{_UNIT}", re.I), "wait_le"),
    (re.compile(rf"{_NUM}{_UNIT}\s+or less\b", re.I), "wait_le"),
    (re.compile(rf"(?:less than|under)\s*{_NUM}{_UNIT}", re.I), "wait_lt"),
    (re.compile(rf"exactly\s*{_NUM}{_UNIT}", re.I), "wait_eq"),
    (re.compile(rf"between\s*{_NUM}\s*and\s*{_NUM}{_UNIT}\s*\((inclusive|exclusive)\)", re.I), "wait_between"),
]
_CONN = re.compile(r"(and|or)\b", re.I)
_NOT = re.compile(r"not\b\s*", re.I)
_FIELDS = {"category", "urgent", "tier", "wait", "waiting", "waited"}


def _minutes(number: str, unit: str | None) -> Fraction:
    if unit is None:
        raise _Problem("U-AMBIG-UNIT", f"wait value {number} has no unit (minutes or hours)")
    u = unit.lower()
    if u in ("minute", "minutes", "min", "mins"):
        return Fraction(number)
    if u in ("hour", "hours", "hr", "hrs", "h"):
        return Fraction(number) * 60
    raise _Problem("U-AMBIG-UNIT", f"unit {unit!r} is not supported (minutes or hours only)")


def _build_atom(kind: str, m: re.Match, text: str) -> Any:
    g = m.groups()
    if kind == "cat_missing":
        return Atom("category", "is_missing")
    if kind == "cat_present":
        return Atom("category", "is_present")
    if kind in ("cat_eq", "cat_ne"):
        v = g[0].strip()
        if not v:
            raise _Problem("U-EMPTY-LITERAL", "empty category literal: empty and missing are the same thing")
        return Atom("category", "eq" if kind == "cat_eq" else "ne", v)
    if kind == "cat_in":
        vals = [v.strip() for v in re.findall(_Q, m.group(0))]
        if any(not v for v in vals):
            raise _Problem("U-EMPTY-LITERAL", "empty category literal: empty and missing are the same thing")
        return Or(tuple(Atom("category", "eq", v) for v in vals))
    if kind == "urgent":
        return Atom("urgent", "eq", (g[0] or "true").lower() == "true")
    if kind == "tier_eq" or kind == "tier_ticket":
        return Atom("tier", "eq", g[0].lower())
    if kind == "tier_ne":
        return Atom("tier", "ne", g[0].lower())
    if kind == "wait_cmp":
        op = {">=": "ge", ">": "gt", "<=": "le", "<": "lt", "=": "eq", "==": "eq", "!=": "ne"}[g[0]]
        return Atom("wait", op, _minutes(g[1], g[2]))
    if kind in ("wait_ge", "wait_gt", "wait_le", "wait_lt", "wait_eq"):
        return Atom("wait", kind[-2:], _minutes(g[0], g[1]))
    if kind == "wait_between":
        lo, hi = _minutes(g[0], g[2]), _minutes(g[1], g[2])
        inclusive = g[3].lower() == "inclusive"
        return And((Atom("wait", "ge" if inclusive else "gt", lo), Atom("wait", "le" if inclusive else "lt", hi)))
    raise AssertionError(kind)


class _Cond:
    def __init__(self, text: str):
        self.s = text
        self.i = 0

    def parse(self) -> Any:
        node = self._level()
        self._ws()
        if self.i != len(self.s):
            raise self._fail()
        return node

    def _ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def _fail(self) -> _Problem:
        rest = self.s[self.i:].strip()
        m = re.match(r"([A-Za-z_]+)\s*(?:=|==|!=|>=|<=|>|<|\bis\b)", rest)
        if m and m.group(1).lower() not in _FIELDS:
            return _Problem("U-UNKNOWN-FIELD", f"unknown field {m.group(1)!r} in condition near {rest[:40]!r}")
        return _Problem("U-PROSE", f"condition text not in the supported language near {rest[:40]!r}")

    def _level(self) -> Any:
        operands = [self._operand()]
        conns: list[str] = []
        while True:
            self._ws()
            m = _CONN.match(self.s, self.i)
            if not m:
                break
            conns.append(m.group(1).lower())
            self.i = m.end()
            operands.append(self._operand())
        if len(set(conns)) > 1:
            raise _Problem("U-AMBIG-LOGIC", "and/or mixed without parentheses; write (a and b) or c")
        if not conns:
            return operands[0]
        return And(tuple(operands)) if conns[0] == "and" else Or(tuple(operands))

    def _operand(self) -> Any:
        self._ws()
        if self.s.startswith("(", self.i):
            self.i += 1
            node = self._level()
            self._ws()
            if not self.s.startswith(")", self.i):
                raise self._fail()
            self.i += 1
            return node
        m = _NOT.match(self.s, self.i)
        if m:
            self.i = m.end()
            return Not(self._operand())
        best: tuple[int, re.Match, str] | None = None
        for pat, kind in _ATOMS:
            am = pat.match(self.s, self.i)
            if am and (best is None or am.end() > best[0]):
                best = (am.end(), am, kind)
        if best is None:
            raise self._fail()
        node = _build_atom(best[2], best[1], self.s)
        self.i = best[0]
        return node


def parse_condition(text: str) -> Any:
    """Parse a condition in the section 4.2 grammar. Raises ``_Problem``."""
    return _Cond(_norm(text)).parse()


# ---- entries ---------------------------------------------------------------------
@dataclass
class ReqEntry:
    id: str
    order: int
    evidence: EvidenceLocation
    text: str
    kind: str = "unparsed"  # rule | exception | default | unparsed
    cond: Any | None = None
    route: str | None = None
    skip: bool = False
    target: str | None = None  # exceptions: the rule id named in the sentence
    otherwise: bool = False
    status: str = "ok"  # ok | ambiguous | unsupported | conflicted
    codes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    extra_codes: list[str] = field(default_factory=list)  # codes of extra, unsupported segments

    def taint(self, status: str, code: str, reason: str) -> None:
        order = {"ok": 0, "ambiguous": 1, "unsupported": 2, "conflicted": 3}
        if order[status] > order[self.status]:
            self.status = status
        if code not in self.codes:
            self.codes.append(code)
        self.reasons.append(reason)


@dataclass
class ParsedRequirements:
    version: str
    entries: list[ReqEntry]
    usable: bool = True  # False: ordering/ids/size make the whole file unreviewable
    global_coverage: list[CoverageEntry] = field(default_factory=list)


def _classify_extra(sentence: str) -> str:
    if _INSTRUCTION.search(sentence):
        return "U-INSTRUCTION"
    if _APPROVAL.search(sentence):
        return "U-APPROVAL-PROSE"
    return "U-PROSE"


def _match_template(sentence: str) -> tuple[str, re.Match] | None:
    for name, pat in (("T4", _T4), ("T5", _T5), ("T3", _T3A), ("T3", _T3B), ("T2", _T2), ("T1", _T1)):
        m = pat.fullmatch(sentence)
        if m:
            return name, m
    return None


def parse_entry(entry: ReqEntry) -> None:
    """Fill ``entry`` from its text: the first template sentence is parsed, every
    other sentence is an unsupported segment (``extra_codes``)."""
    sentences = [s for s in _SPLIT.split(_norm(entry.text)) if s]
    template: tuple[str, re.Match] | None = None
    extras: list[str] = []
    for s in sentences:
        hit = _match_template(s) if template is None else None
        if hit is not None:
            template = hit
        else:
            extras.append(s)
    if template is None:
        entry.kind = "unparsed"
        for s in sentences or [entry.text]:
            entry.taint("unsupported", _classify_extra(s), "text matches no sentence template")
        return
    name, m = template
    d = m.groupdict()
    try:
        if name == "T3":
            entry.kind, entry.route = "default", _dest(d["dest"])
        elif name in ("T4", "T5"):
            if name == "T4" and _norm(d["rid"]) != _norm(d["rid2"]):
                raise _Problem("U-PROSE", "exception names two different rules")
            entry.kind, entry.target = "exception", _norm(d["rid"])
            entry.skip = name == "T4"
            entry.route = None if entry.skip else _dest(d["dest"])
            entry.cond = parse_condition(d["cond"])
        else:
            entry.kind, entry.route = "rule", _dest(d["dest"])
            entry.otherwise = bool(d.get("oth"))
            entry.cond = parse_condition(d["cond"])
    except _Problem as p:
        entry.cond = None
        entry.taint("unsupported" if coverage_status([p.code]) == "unsupported" else "ambiguous", p.code, p.message)
    entry.extra_codes = [_classify_extra(s) for s in extras]
    for s in extras:
        entry.reasons.append(f"extra sentence not reviewed: {s[:80]!r}")


def parse_requirements(doc: RequirementsDoc, vocabulary: list[str] | None = None) -> ParsedRequirements:
    """Parse every entry. ``vocabulary`` (the spec's destinations) flags unknown destinations."""
    entries = [ReqEntry(id=r.id, order=r.order, evidence=r.evidence, text=r.text) for r in doc.requirements]
    issues = doc.issues()
    if issues or len(entries) > SIZE_LIMITS["rules"]:
        codes = sorted({i.code for i in issues}) or ["U-SIZE-LIMIT"]
        reasons = [i.message for i in issues] or [f"{len(entries)} entries; limit {SIZE_LIMITS['rules']}"]
        cov = [CoverageEntry(item=e.id, kind="requirement", status=coverage_status(codes), codes=codes,
                             reason="; ".join(reasons), evidence=e.evidence) for e in entries]
        return ParsedRequirements(doc.version, entries, usable=False, global_coverage=cov)

    for e in entries:
        parse_entry(e)
        if vocabulary is not None and e.route is not None and e.route not in vocabulary:
            e.taint("ambiguous", "U-DEST-UNKNOWN", f"{e.route!r} is not a declared destination")

    rule_ids = {e.id for e in entries if e.kind == "rule"}
    for e in entries:
        if e.kind == "exception" and e.target not in rule_ids:
            e.taint("ambiguous", "U-AMBIG-LOGIC", f"exception target {e.target!r} is not a rule requirement")

    rules = sorted((e for e in entries if e.kind in ("rule", "unparsed")), key=lambda e: e.order)
    if rules and rules[0].kind == "rule" and rules[0].otherwise:
        rules[0].taint("ambiguous", "U-ORDER-AMBIG", "says 'Otherwise' but nothing precedes it")

    glob: list[CoverageEntry] = []
    defaults = [e for e in entries if e.kind == "default"]
    if not defaults:
        glob.append(CoverageEntry(item="default", kind="requirement", status="ambiguous", codes=["U-NO-DEFAULT"],
                                  reason="no default requirement: the route for unmatched tickets is unspecified",
                                  evidence=EvidenceLocation(artifact="requirements", version=doc.version, location="default")))
    elif len(defaults) > 1:
        for e in defaults:
            e.taint("ambiguous", "U-AMBIG-DEFAULT", "more than one default requirement")
    return ParsedRequirements(doc.version, entries, usable=True, global_coverage=glob)
