"""Independent interpreters for fixture artifacts (raw YAML/JSON/Python).

Written separately from ``reviewer/``: they read raw files with yaml/json and
never use the reviewer's schemas or parsers. Python fixtures are interpreted
from an explicit AST subset without compiling or executing submitted code.
"""
from __future__ import annotations

import ast
from functools import lru_cache
import json
import operator
from fractions import Fraction
from pathlib import Path

import yaml


def _minutes(value, unit):
    f = Fraction(str(value))
    return f * 60 if unit == "hours" else f


def ev(cond: dict, t: dict, unit) -> bool:
    if "all" in cond:
        return all(ev(c, t, unit) for c in cond["all"])
    if "any" in cond:
        return any(ev(c, t, unit) for c in cond["any"])
    if "not" in cond:
        return not ev(cond["not"], t, unit)
    f, op = cond["field"], cond["op"]
    if f == "category":
        v = t["category"]
        return {
            "is_missing": lambda: v is None,
            "is_present": lambda: v is not None,
            "eq": lambda: v == cond.get("value"),
            "ne": lambda: v != cond.get("value"),
        }[op]()
    if f in ("urgent", "tier"):
        v = t[f]
        return v == cond["value"] if op == "eq" else v != cond["value"]
    if f == "wait":
        c = _minutes(cond["value"], cond.get("unit") or unit)
        w = t["wait_minutes"]
        return {"eq": w == c, "ne": w != c, "lt": w < c, "le": w <= c, "gt": w > c, "ge": w >= c}[op]
    raise ValueError(f"unknown field {f}")


def run_spec(spec: dict, t: dict):
    unit = (spec.get("units") or {}).get("wait")
    for rule in sorted(spec["rules"], key=lambda r: r["order"]):
        if not ev(rule["when"], t, unit):
            continue
        skipped = False
        for exc in sorted(rule.get("exceptions") or [], key=lambda e: e["order"]):
            if ev(exc["when"], t, unit):
                if exc["action"] == "skip":
                    skipped = True
                    break
                return exc["action"]["route"]
        if not skipped:
            return rule["route"]
    return spec["default"]


def run_workflow(wf: dict, t: dict):
    unit = (wf.get("units") or {}).get("wait")
    nodes = {n["id"]: n for n in wf["nodes"]}
    cur = wf["start"]
    for _ in range(1000):
        n = nodes[cur]
        if n["type"] == "terminal":
            return n["destination"]
        for b in sorted(n["branches"], key=lambda b: b["order"]):
            if "else" in b:
                cur = b["else"]
                break
            if ev(b["when"], t, unit):
                cur = b["to"]
                break
        else:
            return None  # no branch applies: the artifact has no route
    raise RuntimeError("workflow cycle")


_OPERATORS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
    ast.Is: operator.is_, ast.IsNot: operator.is_not,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.In: lambda x, y: x in y, ast.NotIn: lambda x, y: x not in y,
}


def _value(node, values):
    match node:
        case ast.Constant(value=value):
            return Fraction(str(value)) if isinstance(value, float) else value
        case ast.Name(id=name):
            return values[name]
        case ast.Subscript(value=obj, slice=key):
            return _value(obj, values)[_value(key, values)]
        case ast.Call(func=ast.Attribute(value=ast.Name(id="ticket"), attr="get"),
                      args=[ast.Constant(value=str() as key)], keywords=[]):
            return values["ticket"].get(key)
        case ast.UnaryOp(op=ast.Not(), operand=item):
            return not _value(item, values)
        case ast.BoolOp(op=ast.And(), values=items):
            return all(_value(item, values) for item in items)
        case ast.BoolOp(op=ast.Or(), values=items):
            return any(_value(item, values) for item in items)
        case ast.Compare(left=left, ops=[op], comparators=[right]) if type(op) in _OPERATORS:
            return _OPERATORS[type(op)](_value(left, values), _value(right, values))
        case ast.Tuple(elts=items):
            return tuple(_value(item, values) for item in items)
        case _:
            raise ValueError(f"Unsupported fixture expression: {type(node).__name__}")


_FALLTHROUGH = object()


def _block(nodes, values):
    for node in nodes:
        match node:
            case ast.Assign(targets=[ast.Name(id=name)], value=value) if name != "ticket":
                values[name] = _value(value, values)
            case ast.If(test=test, body=yes, orelse=no):
                result = _block(yes if _value(test, values) else no, values)
                if result is not _FALLTHROUGH:
                    return result
            case ast.Return(value=value):
                return _value(value, values)
            case _:
                raise ValueError(f"Unsupported fixture statement: {type(node).__name__}")
    return _FALLTHROUGH


@lru_cache(maxsize=64)
def _fixture_body(source, function):
    match ast.parse(source).body:
        case [ast.FunctionDef(name=name, body=body, decorator_list=[])] if name == function:
            return body
        case _:
            raise ValueError("Expected one undecorated fixture function")


def run_code(source: str, function: str, t: dict):
    ticket = dict(t)
    ticket["wait_hours"] = Fraction(t["wait_minutes"], 60)
    result = _block(_fixture_body(source, function), {"ticket": ticket})
    return None if result is _FALLTHROUGH else result


def load_raw(bundle_dir: str | Path) -> dict:
    root = Path(bundle_dir)
    idx = json.loads((root / "code" / "index.json").read_text())["snippets"]
    return {
        "requirements": yaml.safe_load((root / "requirements.yaml").read_text()),
        "spec": yaml.safe_load((root / "spec.yaml").read_text()),
        "workflow": json.loads((root / "workflow.json").read_text()),
        "approvals": json.loads((root / "approvals.json").read_text()),
        "code": [
            {"id": s["id"], "function": s.get("function", "route"), "source": (root / "code" / s["file"]).read_text()}
            for s in idx
        ],
    }


def runners(raw: dict) -> dict:
    """artifact name -> callable(ticket) -> route or None."""
    out = {
        "spec": lambda t: run_spec(raw["spec"], t),
        "workflow": lambda t: run_workflow(raw["workflow"], t),
    }
    for s in raw["code"]:
        out[f"code:{s['id']}"] = (lambda s: lambda t: run_code(s["source"], s["function"], t))(s)
    return out
