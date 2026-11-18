"""Read one bundle directory into a ``Bundle``. Reads files only: never executes anything.

Layout (see docs/supported_language.md section 1):

    <bundle_id>/requirements.yaml  spec.yaml  workflow.json  approvals.json
                code/index.json    code/<file>.py

A missing, unreadable or invalid artifact becomes a visible ``Issue`` on the
bundle (``U-MISSING-ARTIFACT`` / ``U-SCHEMA``); the artifact is then not reviewed.
An oversized bundle is not truncated: nothing is loaded and ``U-SIZE-LIMIT`` is reported.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import ValidationError

from .schemas import (
    Approvals,
    Bundle,
    CodeSnippet,
    EvidenceLocation,
    Issue,
    RequirementsDoc,
    Spec,
    Workflow,
)

MAX_BUNDLE_BYTES = 256 * 1024


def _issue(code: str, artifact: str, message: str) -> Issue:
    return Issue(
        code=code,
        message=message[:300],
        evidence=EvidenceLocation(artifact=artifact, version="unknown", location="file"),
    )


def _read(path: Path, kind: str) -> Any:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text) if kind == "yaml" else json.loads(text)


def _load_one(root: Path, name: str, kind: str, build: Callable[[Any], Any], issues: list[Issue]) -> Any:
    path = root / name
    if not path.is_file():
        issues.append(_issue("U-MISSING-ARTIFACT", name, f"{name} is missing"))
        return None
    try:
        return build(_read(path, kind))
    except (ValidationError, yaml.YAMLError, json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        issues.append(_issue("U-SCHEMA", name, f"{name} is not valid: {exc}"))
        return None


def _load_code(root: Path, issues: list[Issue]) -> list[CodeSnippet]:
    index_path = root / "code" / "index.json"
    if not index_path.is_file():
        issues.append(_issue("U-MISSING-ARTIFACT", "code/index.json", "code/index.json is missing"))
        return []
    try:
        entries = json.loads(index_path.read_text(encoding="utf-8"))["snippets"]
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError) as exc:
        issues.append(_issue("U-SCHEMA", "code/index.json", f"code/index.json is not valid: {exc}"))
        return []
    code_dir = (root / "code").resolve()
    out: list[CodeSnippet] = []
    for entry in entries:
        try:
            src_path = (code_dir / entry["file"]).resolve()
            if code_dir not in src_path.parents:
                raise ValueError("code file must live inside the bundle's code/ directory")
            out.append(CodeSnippet(**entry, source=src_path.read_text(encoding="utf-8")))
        except (ValidationError, OSError, UnicodeDecodeError, KeyError, TypeError, ValueError) as exc:
            issues.append(_issue("U-SCHEMA", "code/index.json", f"snippet {entry!r} not loaded: {exc}"))
    return out


def _approvals(raw: Any) -> Approvals:
    # A bare JSON array is allowed; it carries no artifact version.
    return Approvals(records=raw) if isinstance(raw, list) else Approvals.model_validate(raw)


def load_bundle(path: str | Path) -> Bundle:
    root = Path(path)
    issues: list[Issue] = []
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    if total > MAX_BUNDLE_BYTES:
        issues.append(_issue("U-SIZE-LIMIT", root.name, f"bundle is {total} bytes; limit is {MAX_BUNDLE_BYTES}; nothing was reviewed"))
        return Bundle(id=root.name, load_issues=issues)
    requirements = _load_one(root, "requirements.yaml", "yaml", RequirementsDoc.model_validate, issues)
    spec = _load_one(root, "spec.yaml", "yaml", Spec.model_validate, issues)
    workflow = _load_one(root, "workflow.json", "json", Workflow.model_validate, issues)
    approvals = _load_one(root, "approvals.json", "json", _approvals, issues)
    code = _load_code(root, issues)
    return Bundle(
        id=root.name,
        requirements=requirements,
        spec=spec,
        workflow=workflow,
        code=code,
        approvals=approvals or Approvals(),
        load_issues=issues,
    )
