"""Verify every label against brute-force evidence.  Run: python -m oracle.check_labels

For each bundle it routes the dense grid through the hand-written policy and
through each artifact (spec, workflow, code) and checks that:
  * valid       -> no artifact disagrees with the policy
  * defective   -> exactly the artifacts named in the label disagree, and each
                   labelled counterexample is a real disagreement
  * incomplete  -> no hidden behavioural defect (artifacts agree with policy)
  * pairs       -> invariant variants route like their base; behaviour-change
                   variants differ from it
  * no label text leaked into reviewer inputs
Exit code 1 on any inconsistency.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from .interpreters import load_raw, runners
from .policy import grid, key, support_v1

ROOT = Path(__file__).resolve().parents[1]
BUNDLES = ROOT / "fixtures" / "bundles"
LABELS = ROOT / "labels"
LEAK = re.compile(r"defective|incomplete|CHANGES_REQUIRED|expected_status|ground[ _]truth|held[- ]?out", re.I)


def tables(raw: dict) -> dict[str, dict]:
    out = {}
    for name, fn in runners(raw).items():
        out[name] = {key(t): fn(t) for t in grid()}
    return out


def disagreements(raw: dict, thr: int) -> dict[str, list]:
    res = {}
    for name, table in tables(raw).items():
        bad = []
        for t in grid():
            exp = support_v1(t, thr)
            act = table[key(t)]
            if exp != act:
                bad.append((t, exp, act))
        res[name] = sorted(bad, key=lambda r: key(r[0]))
    return res


def load_labels() -> dict[str, dict]:
    return {p.stem: yaml.safe_load(p.read_text()) for p in sorted(LABELS.glob("b*.yaml"))}


def check(bundles: Path = BUNDLES, labels_dir: Path = LABELS) -> list[str]:
    errors: list[str] = []
    labels = {p.stem: yaml.safe_load(p.read_text()) for p in sorted(labels_dir.glob("b*.yaml"))}
    tabs: dict[str, dict] = {}
    for bid, lab in labels.items():
        d = bundles / bid
        if not d.is_dir():
            errors.append(f"{bid}: bundle directory missing")
            continue
        for f in d.rglob("*"):
            if f.is_file() and LEAK.search(f.read_text()):
                errors.append(f"{bid}: label-like text leaked into {f.relative_to(d)}")
        raw = load_raw(d)
        thr = lab["intended_policy"]["wait_threshold_minutes"]
        dis = disagreements(raw, thr)
        tabs[bid] = tables(raw)
        bad = {a for a, v in dis.items() if v}
        want = {x["oracle_artifact"] for x in lab.get("expected_findings", []) if x.get("behavioural", True)}
        ctype = lab["case_type"]
        if ctype in ("valid", "incomplete") and bad:
            errors.append(f"{bid}: {ctype} case but artifacts disagree with policy: {sorted(bad)}")
        if ctype == "defective":
            if bad != want:
                errors.append(f"{bid}: disagreeing artifacts {sorted(bad)} != labelled {sorted(want)}")
            for x in lab["expected_findings"]:
                ce = x.get("counterexample")
                if not ce:
                    errors.append(f"{bid}: defective finding without counterexample")
                    continue
                hit = [r for r in dis.get(x["oracle_artifact"], []) if r[0] == ce["ticket"] and r[1] == ce["expected_route"] and r[2] == ce["actual_route"]]
                if not hit:
                    errors.append(f"{bid}: labelled counterexample is not a real disagreement: {ce}")
        if lab["expected_status"] != {"valid": "PASS", "defective": "CHANGES_REQUIRED", "incomplete": "INCOMPLETE"}[ctype]:
            errors.append(f"{bid}: status does not match case type")
    for bid, lab in labels.items():
        pair = lab.get("pair")
        if not pair or pair["base"] not in tabs or bid not in tabs:
            continue
        def sig(b):  # behaviour signature, independent of artifact ids
            t = tabs[b]
            return (t["spec"], t["workflow"], sorted((str(sorted(v.items())) for k, v in t.items() if k.startswith("code:"))))

        differs = sig(bid) != sig(pair["base"])
        if pair["kind"] == "invariant" and differs:
            errors.append(f"{bid}: labelled invariant pair of {pair['base']} but behaviour differs")
        if pair["kind"] == "behavior_change" and not differs:
            errors.append(f"{bid}: labelled behaviour change of {pair['base']} but behaviour is identical")
    return errors


def main() -> int:
    errs = check()
    n = len(list(LABELS.glob("b*.yaml")))
    if errs:
        print("\n".join(errs))
        print(f"FAILED: {len(errs)} inconsistencies in {n} labels")
        return 1
    print(f"OK: {n} labels consistent with brute-force oracle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
