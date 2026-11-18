"""Lower a ``CodeSnippet`` to a ``CodeIR`` (Step 5). Uses ``ast`` only: the source
is parsed into a tree and translated; it is never compiled, executed or evaluated.

Supported subset (docs/supported_language.md section 7): one function, ``if``/
``elif``/``else``, ``return "<dest>"``, simple local aliases of ticket reads,
comparisons, ``and``/``or``/``not``, literal constants. Anything else makes the
snippet *not reviewed* with ``U-PY-CONSTRUCT`` (or ``U-PY-SYNTAX`` if it does not
parse). Comments, docstrings and variable names carry no meaning here.

Syntax checking, inspection and behaviour are separate: ``syntax_ok`` and
``inspection_ok`` report the first two; Step 6 compares behaviour.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from fractions import Fraction

from .ir import (
    SIZE_LIMITS, And, Atom, CodeIR, Const, If, Lowered, Not, Or, Return, coverage_status,
)
from .schemas import CodeSnippet, CoverageEntry, EvidenceLocation, Issue

FIELD_KEYS = {"category", "urgent", "tier", "wait_minutes", "wait_hours"}
_CMP_OPS = {ast.Lt: "lt", ast.LtE: "le", ast.Gt: "gt", ast.GtE: "ge",
            ast.Eq: "eq", ast.NotEq: "ne", ast.Is: "eq", ast.IsNot: "ne"}
_FLIP = {"lt": "gt", "gt": "lt", "le": "ge", "ge": "le", "eq": "eq", "ne": "ne"}


class _Unsupported(Exception):
    def __init__(self, message: str, node: ast.AST | None = None, code: str = "U-PY-CONSTRUCT"):
        super().__init__(message)
        self.message = message
        self.line = getattr(node, "lineno", None)
        self.code = code


def _is_str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


class _Translator:
    def __init__(self, function: str) -> None:
        self.function = function
        self.param = ""
        self.aliases: dict[str, str] = {}

    # -- structure ----------------------------------------------------------
    def module(self, tree: ast.Module) -> tuple[list, tuple[int, int]]:
        body = list(tree.body)
        if body and isinstance(body[0], ast.Expr) and _is_str(body[0].value):
            body = body[1:]
        if len(body) != 1 or not isinstance(body[0], ast.FunctionDef) or body[0].name != self.function:
            raise _Unsupported(f"expected exactly one function named {self.function!r} and nothing else", body[0] if body else None)
        fn = body[0]
        a = fn.args
        if fn.decorator_list or len(a.args) != 1 or a.vararg or a.kwarg or a.kwonlyargs or a.defaults or a.posonlyargs:
            raise _Unsupported("the function must take exactly one plain parameter and have no decorators", fn)
        self.param = a.args[0].arg
        return self.block(fn.body), (fn.lineno, fn.end_lineno or fn.lineno)

    def block(self, stmts: list[ast.stmt]) -> list:
        out: list = []
        for i, s in enumerate(stmts):
            if i == 0 and isinstance(s, ast.Expr) and _is_str(s.value):
                continue  # docstring
            if isinstance(s, ast.Assign):
                self.alias(s)
            elif isinstance(s, ast.If):
                out.append(If(self.cond(s.test), self.block(s.body), self.block(s.orelse), s.lineno))
            elif isinstance(s, ast.Return):
                if not _is_str(s.value):
                    raise _Unsupported("return must give a string literal destination", s)
                out.append(Return(s.value.value, s.lineno, s.end_lineno or s.lineno))
            else:
                raise _Unsupported(f"{type(s).__name__} statement is outside the supported subset", s)
        return out

    def alias(self, s: ast.Assign) -> None:
        if len(s.targets) != 1 or not isinstance(s.targets[0], ast.Name):
            raise _Unsupported("only a simple `name = ticket[...]` assignment is supported", s)
        name = s.targets[0].id
        key = self.read(s.value)
        if key is None:
            raise _Unsupported("an assignment must copy a ticket field (ticket[\"x\"] or ticket.get(\"x\"))", s)
        if name == self.param or name in self.aliases:
            raise _Unsupported(f"{name!r} is assigned more than once", s)
        self.aliases[name] = key

    # -- reads ----------------------------------------------------------------
    def read(self, e: ast.AST) -> str | None:
        """Field key if ``e`` reads a ticket field, else None."""
        key: str | None = None
        if isinstance(e, ast.Name) and e.id in self.aliases:
            return self.aliases[e.id]
        if isinstance(e, ast.Subscript) and isinstance(e.value, ast.Name) and e.value.id == self.param and _is_str(e.slice):
            key = e.slice.value
        elif (isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and e.func.attr == "get"
              and isinstance(e.func.value, ast.Name) and e.func.value.id == self.param and not e.keywords
              and 1 <= len(e.args) <= 2 and _is_str(e.args[0])
              and (len(e.args) == 1 or (isinstance(e.args[1], ast.Constant) and e.args[1].value is None))):
            key = e.args[0].value
        if key is None:
            return None
        if key not in FIELD_KEYS:
            raise _Unsupported(f"unknown ticket field {key!r}", e, "U-UNKNOWN-FIELD")
        return key

    # -- conditions -------------------------------------------------------------
    def cond(self, e: ast.expr):
        if isinstance(e, ast.BoolOp):
            items = tuple(self.cond(v) for v in e.values)
            return And(items) if isinstance(e.op, ast.And) else Or(items)
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not):
            return Not(self.cond(e.operand))
        if isinstance(e, ast.Constant) and isinstance(e.value, bool):
            return Const(e.value)
        if isinstance(e, ast.Compare):
            return self.compare(e)
        key = self.read(e)
        if key == "category":
            return Atom("category", "is_present")
        if key == "urgent":
            return Atom("urgent", "eq", True)
        if key is not None:
            raise _Unsupported(f"truthiness of {key!r} is not supported; compare it explicitly", e)
        raise _Unsupported(f"{type(e).__name__} expression is outside the supported subset", e)

    @staticmethod
    def const(e: ast.AST):
        if isinstance(e, ast.Constant):
            return e.value
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.USub) and isinstance(e.operand, ast.Constant) \
                and isinstance(e.operand.value, (int, float)) and not isinstance(e.operand.value, bool):
            return -e.operand.value
        if isinstance(e, (ast.Tuple, ast.List, ast.Set)) and e.elts and all(isinstance(x, ast.Constant) for x in e.elts):
            return tuple(x.value for x in e.elts)
        raise _Unsupported("compared value must be a literal constant", e)

    def compare(self, e: ast.Compare):
        if len(e.ops) != 1 or len(e.comparators) != 1:
            raise _Unsupported("chained comparisons are not supported", e)
        op_t = type(e.ops[0])
        left, right = e.left, e.comparators[0]
        lk, rk = self.read(left), self.read(right)
        if lk and rk or (not lk and not rk):
            raise _Unsupported("a comparison needs one ticket field and one literal constant", e)
        membership = op_t in (ast.In, ast.NotIn)
        if not lk:  # constant on the left: flip
            if membership:
                raise _Unsupported("`in` needs the ticket field on the left", e)
            left, right, lk = right, left, rk
            op = _FLIP[_CMP_OPS[op_t]]
            is_identity = op_t in (ast.Is, ast.IsNot)
        else:
            op = None if membership else _CMP_OPS.get(op_t)
            is_identity = op_t in (ast.Is, ast.IsNot)
        if op is None and not membership:
            raise _Unsupported(f"operator {op_t.__name__} is not supported", e)
        value = self.const(right)

        if lk == "category":
            if membership:
                if not (isinstance(value, tuple) and all(isinstance(v, str) for v in value)):
                    raise _Unsupported("`in` on category needs a literal tuple of strings", e)
                if any(not v.strip() for v in value):
                    raise _Unsupported("empty category literal: empty and missing are the same thing", e, "U-EMPTY-LITERAL")
                inner = Or(tuple(Atom("category", "eq", v.strip()) for v in value))
                return Not(inner) if op_t is ast.NotIn else inner
            if value is None:
                return Atom("category", "is_missing" if op == "eq" else "is_present")
            if isinstance(value, str) and not is_identity:
                if not value.strip():
                    raise _Unsupported("empty category literal: empty and missing are the same thing", e, "U-EMPTY-LITERAL")
                return Atom("category", op, value.strip())
            raise _Unsupported("category can be compared with None or a string using ==, !=, in, not in", e)
        if lk == "urgent":
            if membership or op not in ("eq", "ne") or not isinstance(value, bool):
                raise _Unsupported("urgent can be compared with True or False using ==, !=, is, is not", e)
            return Atom("urgent", op, value)
        if lk == "tier":
            if membership:
                if not (isinstance(value, tuple) and all(isinstance(v, str) for v in value)):
                    raise _Unsupported("`in` on tier needs a literal tuple of strings", e)
                inner = Or(tuple(Atom("tier", "eq", v) for v in value))
                return Not(inner) if op_t is ast.NotIn else inner
            if op not in ("eq", "ne") or not isinstance(value, str) or is_identity:
                raise _Unsupported("tier can be compared with a string using == or !=", e)
            return Atom("tier", op, value)
        # wait_minutes / wait_hours
        if membership or is_identity or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _Unsupported(f"{lk} can be compared with a number using ==, !=, <, <=, >, >=", e)
        minutes = Fraction(str(value)) * (60 if lk == "wait_hours" else 1)
        return Atom("wait", op, minutes)


@dataclass
class LoweredCode(Lowered):
    syntax_ok: bool | None = None  # None = not checked (size limit)
    inspection_ok: bool | None = None
    snippet_id: str = ""


def _loc(snippet: CodeSnippet, line: int | None) -> EvidenceLocation:
    n = line or 1
    return EvidenceLocation(artifact=f"code:{snippet.file}", version=snippet.version, location=f"L{n}-L{n}")


def _cov(snippet: CodeSnippet, codes: list[str], reasons: list[str], line: int | None) -> CoverageEntry:
    return CoverageEntry(item=snippet.id, kind="code", status=coverage_status(codes), codes=sorted(set(codes)),
                         reason="; ".join(reasons), evidence=_loc(snippet, line))


def lower_code(snippet: CodeSnippet) -> LoweredCode:
    if snippet.line_count > SIZE_LIMITS["code_lines"]:
        reason = f"{snippet.line_count} lines; limit {SIZE_LIMITS['code_lines']}"
        return LoweredCode(None, [_cov(snippet, ["U-SIZE-LIMIT"], [reason], 1)],
                           [Issue(code="U-SIZE-LIMIT", message=reason, evidence=snippet.evidence)],
                           snippet_id=snippet.id)
    try:
        tree = ast.parse(snippet.source)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        line = getattr(exc, "lineno", None)
        msg = f"source does not parse: {type(exc).__name__}: {exc}"[:200]
        return LoweredCode(None, [_cov(snippet, ["U-PY-SYNTAX"], [msg], line)],
                           [Issue(code="U-PY-SYNTAX", message=msg, evidence=_loc(snippet, line))],
                           syntax_ok=False, inspection_ok=False, snippet_id=snippet.id)
    tr = _Translator(snippet.function)
    try:
        body, span = tr.module(tree)
    except _Unsupported as exc:
        return LoweredCode(None, [_cov(snippet, [exc.code], [exc.message], exc.line)],
                           [Issue(code=exc.code, message=exc.message, evidence=_loc(snippet, exc.line))],
                           syntax_ok=True, inspection_ok=False, snippet_id=snippet.id)
    ir = CodeIR(artifact=f"code:{snippet.file}", version=snippet.version, function_lines=span, body=body)
    return LoweredCode(ir, [], [], syntax_ok=True, inspection_ok=True, snippet_id=snippet.id)
