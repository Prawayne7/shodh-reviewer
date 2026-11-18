# Evaluation dataset (Step 4)

23 synthetic review bundles in `fixtures/bundles/b01 ... b23`, labels in
`labels/b01.yaml ...`. Everything is produced by `python -m fixtures.generate`
from `configs/seed.yaml` (seed 20261118) and verified by an independent oracle.
Labels were fixed **before any reviewer code exists**.

## Composition

| Type | Count | Bundles |
|---|---|---|
| Valid (expected `PASS`) | 7 | b01 b02 b03 b04 b05 b06 b22 |
| Defective (expected `CHANGES_REQUIRED`) | 11 | b07 b08 b09 b10 b11 b12 b13 b14 b15 b21 b23 |
| Incomplete (expected `INCOMPLETE`) | 5 | b16 b17 b18 b19 b20 |

Total 7 + 11 + 5 = 23.

| Id | Type | Split | What it tests |
|---|---|---|---|
| b01 | valid | dev | Baseline R1-R4 with exceptions |
| b02 | valid | dev | Approved change 30 -> 25 applied everywhere |
| b03 | valid | **held-out** | Unusual formulation: hours, negation, folded exception, split workflow, `ticket.get`; pending suggestion ignored |
| b04 | valid | dev | Injection in code comment, spec comment and approval note (must not matter) |
| b05 | valid | dev | b01 with all ids renamed and records reordered (**invariant pair**) |
| b06 | valid | dev | b02 paraphrased within the language, approval id renamed (**invariant pair**) |
| b07 | defective | dev | Spec threshold 35 (**behaviour-change pair** of b01) |
| b08 | defective | dev | Unapproved suggestion (25) implemented in spec, workflow and code |
| b09 | defective | dev | Approved 25 missing in the workflow (**behaviour-change pair** of b02) |
| b10 | defective | **held-out** | Precedence swapped in the spec, metadata still correct |
| b11 | defective | dev | Code `> 30` instead of `>= 30` (**behaviour-change pair** of b01) |
| b12 | defective | dev | Workflow decision node with no `else` (tickets get no route) |
| b13 | defective | dev | Spec omits the missing-category rule (no id to match) |
| b14 | defective | dev | Spec threshold declared in hours (30 h = 1800 min) |
| b15 | defective | **held-out** | Multiple individually plausible rules: only spam + urgent + vip + long wait misroutes |
| b16 | incomplete | **held-out** | Two approved records contradict each other |
| b17 | incomplete | dev | R3 is free prose (unsupported) |
| b18 | incomplete | dev | Wait threshold without a unit (ambiguous) |
| b19 | incomplete | **held-out** | Clean bundle; fake model times out on every call |
| b20 | incomplete | dev | Injection and a prose approval claim inside requirement text |
| b21 | defective | **held-out** | b11-style code defect plus injected instructions (must still be blocked) |
| b22 | valid | dev | Approval chain 30 -> 25 -> 20 with reversed array order, an inapplicable record and a rejected record |
| b23 | defective | dev | Code defect; fake model returns an invented rule reference (blocker must survive) |

Coverage of the assessment list: approved and unapproved changes (b02, b03,
b08, b09, b22), approval conflict (b16), missing requirement (b13),
boundaries (b07, b11), units (b14, b18), precedence (b10), wrong branch (b09,
b12), code disagreement (b08, b11, b21), injected instructions (b04, b20, b21),
model failures (b19, b23), valid unusual formulation (b03), multi-rule defect
(b15), paired variants (b05, b06 invariant; b07, b09, b11 behaviour-changing).

## Held-out split

b03, b10, b15, b16, b21 (5 of 23: 1 valid, 3 defective, 1 incomplete). b19 was moved to development after the configured model-timeout behavior was tuned against it; it is not counted as held-out evidence.
They must not be used to tune the reviewer. `python -m oracle.freeze` wrote
`labels/heldout_manifest.json` (SHA-256 of each held-out label and bundle, the
seed config, the generator and the oracle policy, plus the git commit).
`python -m oracle.freeze --verify` fails if any of it changed, and a unit test
runs it. The reviewer implementation checkpoint is recorded in `reports/` when
it is tagged.

**Development evidence declaration.** I wrote and inspected all 23 bundles
while building the dataset (labels are hand-designed, so every bundle was seen
by the author). What the held-out split protects is *tuning*: no reviewer
behaviour will be adjusted after looking at held-out results. Dev bundles are
development evidence.

## Where labels come from (not circular)

1. **Intended policy** `oracle/policy.py::support_v1` is a hand-written
   function with the wait threshold as a parameter. The threshold of each case
   is stated in its label (`intended_policy.wait_threshold_minutes`), derived
   from the approval rules by hand (see `manual_reasoning`).
2. **Independent interpreters** `oracle/interpreters.py` run each spec,
   workflow and code file on a dense brute-force grid (every minute 0..140 plus
   1799-1801, 4 categories, urgent, tier: 2,304 tickets per artifact). They do
   not import `reviewer/`.
3. **Label check** `python -m oracle.check_labels` asserts: valid and
   incomplete bundles have no artifact disagreeing with the policy; defective
   bundles have disagreements in exactly the labelled artifacts; every labelled
   counterexample is a real disagreement; invariant pairs route identically
   to their base and behaviour-change pairs differ; no label-like text sits in
   any bundle. Corrupting a label or a bundle makes it fail (checked).
4. `INCOMPLETE` labels rest on manual reasoning (unsupported prose, missing
   unit, contradictory approvals, model failure), stated in each label.

The reviewer never imports `oracle/` or reads `labels/` (unit test).
At evaluation time the oracle may validate counterexamples the reviewer
reports; that is checking, not input.

## Label format (`labels/bNN.yaml`)

`case_type`, `expected_status`, `split`, `tags`, `intended_policy`,
`pair {base, kind}`, `model_script`, `expected_findings[]` (kind,
`acceptable_kinds`, severity, `locations` = any-of evidence prefixes,
`rule_refs` = any-of, `counterexample` from the oracle), `expected_coverage[]`
and `manual_reasoning`. Model behaviour for fake-client cases is a harness
input in `fixtures/model_scripts/bNN.json`, not a label.

## Dataset limits

Synthetic, one policy family (`support_v1`) with parameter changes; results
show behaviour on this family only, not on arbitrary specifications.

## Development-evidence log

Every bundle the author or an assistant looked at while building or testing the
reviewer is development evidence. The held-out split protects *tuning*; this log
says exactly what was looked at and for what.

| When | What was inspected | Purpose | Changes caused |
|---|---|---|---|
| Step 4 | all 23 bundles and labels | authored by hand (labels are hand-designed, so every bundle was seen) | none |
| Step 4 review (AI-assisted) | all bundles loaded via the Step 3 loader; every label location checked against its bundle; every requirement sentence listed; every distinct code file read; read in full: b03 (all artifacts), b05, b14, b10 and b15 (spec), b16 (approvals, label), b19 (label, model script), b21 (code, requirement text) | verify contracts, labels and grammar line up | none (`oracle.freeze --verify` still passes) |
| Step 5 tests | dev bundles only (b01, b02, b04-b09, b11-b14, b17, b18, b20, b22, b23): parsed artifacts compared with the independent oracle interpreters on the 2,304-ticket grid; effective requirements compared with the hand-written policy | parser and approval logic correctness | none to any bundle or label |
| Step 5 smoke | held-out bundles: load, parse and evaluate without raising | crash check only; **no routing, coverage or verdict compared** | none |
| Step 7 model-failure tuning | b19 (timeout script and expected INCOMPLETE behavior) | configure required model-check failure semantics and bounded timeout handling | b19 reclassified as development evidence; held-out manifest regenerated without b19 |

Rule going forward: a reviewer change prompted by a held-out result is
development evidence and must be added here before the final run.

