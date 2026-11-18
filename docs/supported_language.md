# Supported language and contracts

This document fixes what the reviewer understands. Everything outside it is
**not verified**; it is reported as `unsupported` or `ambiguous` in coverage
and can push the result to `INCOMPLETE`, never to `PASS`.

"Verified" in this project means: *exhaustively compared over the finite
ticket domain in section 2, for artifacts written in the subsets below.*
It never means "the prose or Python is correct in general".

Contracts are implemented as pydantic models in `reviewer/schemas.py`
(table at the end of section 1) and tested in `tests/test_schemas.py`.

---------------------------------------------------------------------------

## 1. Artifacts and bundle layout

| Artifact | Format | Versioned by | Role |
|---|---|---|---|
| Source requirements | YAML | `version` (e.g. `req-v1`) | Written policy, one anchored requirement per entry |
| Specification | YAML | `version` | Structured implementation of the policy |
| Workflow | JSON | `version` (a node may carry its own) | Versioned graph of decision nodes and branches |
| Code snippets | `.py` files + JSON index | `version` per snippet | Linked Python that routes tickets |
| Approvals | JSON array or `{version, records}` (top level of bundle) | `version` if wrapped, else `unversioned` | The only trusted source of approved changes |

On disk, one directory per bundle (read by `reviewer/loader.py`):

```
fixtures/bundles/<bundle_id>/
    requirements.yaml   spec.yaml   workflow.json   approvals.json
    code/index.json     code/<file>.py
labels/<bundle_id>.yaml        # expected outcome; never read by reviewer/
```

- `bundle_id` is **opaque** (`b01`, `b02`, ...). It never contains words like
  `valid` or `defective`; descriptive names live only in `labels/`.
- Every record may carry a free-text `note`. Notes are stored, never parsed
  and never trusted (section 11).
- Unknown keys are a schema error: that artifact is `not_reviewed(U-SCHEMA)`.
  A missing file is `U-MISSING-ARTIFACT`. Nothing is dropped silently.
- Code index entry: `{id, file, function, version, refs}`. `refs` is a hint.

**Evidence format** `artifact@version#location`. Locations are built from ids
and `order` values, **never from array positions**, so reordering serialized
records cannot change a citation:

| Artifact | Example |
|---|---|
| requirement | `requirements@req-v1#R3` |
| spec rule / exception / default | `spec@spec-v3#S2`, `spec@spec-v3#S2.E1`, `spec@spec-v3#default` |
| workflow branch | `workflow@wf-v2#n3.b2` (node `n3`, branch with `order: 2`) |
| code lines | `code:route.py@c-v1#L12-L14` |
| approval | `approvals@unversioned#A1` |

| Contract | Model in `reviewer/schemas.py` |
|---|---|
| Source requirement | `SourceRequirement`, `RequirementsDoc` |
| Specification | `Spec`, `SpecRule`, `SpecException` |
| Workflow | `Workflow`, `WorkflowNode`, `Branch` |
| Linked code | `CodeSnippet` |
| Approval record | `ApprovalRecord`, `ApprovalChange`, `Approvals` |
| Whole input | `Bundle` (no label field exists) |
| Output | `Finding`, `Counterexample`, `Coverage`, `CheckResult`, `Report` |

---------------------------------------------------------------------------

## 2. Ticket domain (what "exhaustive" ranges over)

A ticket has four fields:

| Field | Domain | Notes |
|---|---|---|
| `category` | `null` or a non-empty string | `""`, whitespace-only and absent are normalised to `null` ("missing") at ingestion. Strings are compared case-sensitively after trimming. |
| `urgent` | `true` / `false` | Always present. |
| `tier` | `"standard"` or `"vip"` | Always present. |
| `wait_minutes` | integer >= 0 | Canonical unit is minutes. Hours are converted (1 h = 60 min). |

**Missing category and comparisons.** For a missing category, `eq "x"` is
false and `ne "x"` is true (the same as Python's `None == "x"` and
`None != "x"`); `is_missing` is true.

**Canonical tickets.** Code is judged over *canonical* tickets (the table
above). Real Python would treat `"   "` as truthy; here it is already `null`.
An empty-string literal (`category == ""`) in any artifact is
`U-EMPTY-LITERAL`: empty and missing are the same thing, so the author's
intent is ambiguous.

Destinations match `[a-z_]+` (e.g. `manual_review`, `priority_queue`,
`escalation`, `normal_queue`). Each bundle's spec declares its destination
vocabulary; a destination outside it is `U-DEST-UNKNOWN`.

---------------------------------------------------------------------------

## 3. Rule model and evaluation semantics

All artifacts are lowered to one internal form: an **ordered decision list**.

```
rules   = [Rule1, Rule2, ...]  sorted by explicit `order`
default = <destination>
Rule      = { id, condition, destination, exceptions: [Exception, ...] }
Exception = { id, condition, action }   action = skip | route(<destination>)
```

Evaluation of a ticket:

1. Walk rules in order. A rule *matches* if its condition is true.
2. On a match, check its exceptions in order; the first exception whose
   condition is true decides:
   - `skip` -> the rule does **not** fire; continue with the next rule.
   - `route(d)` -> the ticket goes to `d`; stop.
   If no exception is true, the ticket goes to the rule's destination; stop.
3. If no rule fires, the ticket goes to `default`.

### 3.1 Ordering contract

**Array position carries no meaning.** Every ordered collection has an
explicit integer `order >= 1`, sorted ascending:

| Collection | `order` is unique within |
|---|---|
| requirement entries | the requirements file |
| spec rules | the spec |
| spec exceptions | their rule |
| workflow branches | their node |

Gaps are fine. A duplicate `order` makes that collection `U-ORDER-AMBIG`
(not reviewed); the reviewer never picks one. This is what lets a paired
variant *reorder serialized records* without changing semantics: shuffle the
array, keep the `order` values, the verdict must not change. Code needs no
`order`: the textual order of its `if`/`elif` chain is the order.

"Otherwise" in prose is **only an ordering marker**. It adds no condition by
itself; precedence comes from `order`. An entry that says "Otherwise," but has
the lowest `order` among rules (nothing precedes it) is `U-ORDER-AMBIG`:
ambiguous input, not a precedence defect.

### 3.2 Unknown barrier (content that could not be reviewed)

An element that is unsupported, ambiguous or conflicted (a requirement, a spec
rule or exception, a workflow branch) is **unknown**: not absent and not
guessed. Evaluating a ticket returns `UNKNOWN` when the walk reaches an unknown
element before a reviewed element decides it:

- tickets decided by reviewed elements that come *before* the unknown one keep
  their route;
- every other ticket that reaches it, including tickets that would have fallen
  through to a later rule or the default, is `UNKNOWN`;
- an unknown exception makes `UNKNOWN` the result for tickets that match its
  rule's condition and were not decided by an earlier exception;
- a missing, unsupported or ambiguous default makes `UNKNOWN` the result for
  tickets no rule fired for;
- an exception whose target rule cannot be found acts as an unknown rule at its
  own `order`.

`UNKNOWN` is never a destination and never a mismatch. Comparison excludes
those tickets, lists the element in coverage, and cannot return `PASS`. Without
this rule a free-prose R3 would leave the default rule "expecting
`normal_queue`" for long-waiting tickets and produce a false blocker.

**Partial review.** A requirement with one template sentence plus extra
unsupported sentences has its template sentence parsed and compared; the extra
sentences stay in coverage as unsupported (docs section 4.1, 11).

---------------------------------------------------------------------------

## 4. Source-requirement language (YAML + restricted English)

```yaml
version: req-v1
requirements:
  - id: R1
    order: 1
    text: "Missing category -> manual review."
  - id: R2
    order: 2
    text: "Otherwise, urgent ticket -> priority queue."
```

### 4.1 Sentence templates (case-insensitive, whitespace-normalised)

| Id | Template | Meaning |
|---|---|---|
| T1 | `[Otherwise,] <cond> -> <dest>.` | Rule |
| T2 | `[Otherwise,] if <cond>, route to <dest>.` | Rule |
| T3 | `All remaining tickets -> <dest>.` or `Otherwise, route to <dest>.` | Default |
| T4 | `Exception to <RID>: if <cond>, do not apply <RID>.` | Skip exception |
| T5 | `Exception to <RID>: if <cond>, route to <dest>.` | Redirect exception |

An exception is its own entry with its own `id` (convention `<RID>.E1`) and
`order`; its **target comes from the sentence (`<RID>`), not from the id**.
`<dest>` is written with spaces or underscores (`priority queue` =
`priority_queue`).

**Segmentation.** `text` is split into sentences at `.` followed by
whitespace or the end (so `0.5 hours` is not split). Each entry should hold
exactly one template sentence. That sentence is parsed; every other sentence
is a separate unsupported segment (`U-PROSE`, section 11) that stays in
coverage. The entry is then *partially reviewed*: the parsed part is still
compared (a real defect is still found), and the extra segment blocks `PASS`.
A second template sentence in one entry is also an unsupported segment
(write it as its own entry).

**Default.** Exactly one T3 entry is expected. None -> `U-NO-DEFAULT`; more
than one -> `U-AMBIG-DEFAULT`. Either way the expected route for tickets that
match no rule is unspecified, so those tickets are not compared and the
requirement is listed `ambiguous`.

### 4.2 Condition grammar

```
cond    := or_expr
or_expr := and_expr ( "or" and_expr )*
and_expr:= not_expr ( "and" not_expr )*
not_expr:= "not" not_expr | "(" cond ")" | atom
```

**Mixing `and` and `or` at the same level without parentheses is
ambiguous and is NOT guessed.** Write `(a and b) or c`. (Python code needs no
such rule: `ast` already fixes its precedence.)

### 4.3 Atoms

| Field | Accepted phrasings | Normalised form |
|---|---|---|
| category | `category is missing`, `missing category`, `no category` | `category is_missing` |
| category | `category is present`, `has a category` | `category is_present` |
| category | `category = "x"`, `category is "x"`, `category equals "x"` | `category eq x` |
| category | `category != "x"`, `category is not "x"` | `category ne x` |
| category | `category is one of "a", "b"` | `any(eq a, eq b)` |
| urgent | `urgent`, `urgent ticket`, `urgent = true` | `urgent eq true` |
| urgent | `not urgent`, `urgent = false` | `urgent eq false` |
| tier | `tier = vip`, `tier is standard`, `vip ticket` | `tier eq vip` |
| wait | `waiting >= N <unit>`, `wait >= N <unit>`, `at least N <unit>`, `N <unit> or more` | `wait ge N` |
| wait | `> N <unit>`, `more than N <unit>`, `over N <unit>` | `wait gt N` |
| wait | `<= N <unit>`, `at most N <unit>`, `N <unit> or less` | `wait le N` |
| wait | `< N <unit>`, `less than N <unit>`, `under N <unit>` | `wait lt N` |
| wait | `= N <unit>`, `exactly N <unit>` | `wait eq N` |
| wait | `between A and B <unit> (inclusive)` / `(exclusive)` | `and(ge A, le B)` / `and(gt A, lt B)` |

`<unit>` is `minutes`/`minute`/`min`/`mins` or `hours`/`hour`/`h`.
`N` is a non-negative decimal number.

---------------------------------------------------------------------------

## 5. Structured specification (YAML)

```yaml
version: spec-v3
destinations: [manual_review, priority_queue, escalation, normal_queue]
units: { wait: minutes }          # declared unit for every wait value
default: normal_queue
rules:
  - id: S1
    order: 1
    refs: [R1]                    # hint only, never trusted for matching
    when: { field: category, op: is_missing }
    route: manual_review
  - id: S2
    order: 2
    refs: [R2]
    when: { field: urgent, op: eq, value: true }
    route: priority_queue
    exceptions:
      - order: 1
        when: { field: category, op: eq, value: spam }
        action: skip
  - id: S3
    order: 3
    refs: [R3]
    when: { field: wait, op: ge, value: 30, unit: minutes }
    route: escalation
    exceptions:
      - order: 1
        when: { field: tier, op: eq, value: vip }
        action: { route: priority_queue }
```

Condition AST: leaf `{field, op, value, unit?}` with
`op in {eq, ne, lt, le, gt, ge, is_missing, is_present}`; combinators
`{all: [...]}`, `{any: [...]}`, `{not: <cond>}`.

A leaf is **well formed** when: `field` is one of the four ticket fields
(`U-UNKNOWN-FIELD`), `op` is known (`U-UNKNOWN-OP`), the operator is valid for
the field, `is_missing`/`is_present` take no value and the others need one,
and the value has the right type (all `U-LEAF-SHAPE`); `wait` needs a
`minutes`/`hours` unit on the leaf or declared by the artifact's `units`
(`U-AMBIG-UNIT`). Values are never coerced: `"30"` is a string, not 30.

---------------------------------------------------------------------------

## 6. Workflow (JSON)

```json
{
  "version": "wf-v2", "start": "n1", "units": {"wait": "minutes"},
  "nodes": [
    {"id": "n1", "type": "decision", "refs": ["R1"], "branches": [
        {"order": 1, "when": {"field": "category", "op": "is_missing"}, "to": "t_manual"},
        {"order": 2, "else": "n2"}]},
    {"id": "t_manual", "type": "terminal", "destination": "manual_review"}
  ]
}
```

- Branches of a decision node are evaluated **in `order`**; first true branch
  wins; `else` is the fallback and must have the highest `order`.
  Conditions use the section 5 AST.
- A branch is either `{when, to}` or `{else}`; a decision node has branches,
  a terminal node has a `destination` (otherwise a schema error).
- Node versions: a node may carry its own `version`; a finding cites the
  node's version if present, else the workflow's.
- Structural checks (`U-WF-STRUCTURE`): start node exists, branch targets
  exist, at most one `else` and it is last; the graph must be acyclic and
  every node reachable from `start` (checked when the graph is walked).
- **Missing branch**: a decision node with no `else` whose branch conditions do
  not cover every ticket reaching it. The reviewer reports a concrete
  uncovered ticket.
- Exceptions are expressed as ordinary branches placed earlier/later in the
  graph; the reviewer judges behaviour, not shape.

---------------------------------------------------------------------------

## 7. Python subset (parsed with `ast`, never executed)

A snippet is a **complete router**: one function `route(ticket)` returning a
string literal. If a bundle has several snippets, each is compared
independently against the effective requirements.

**Supported**

- `if` / `elif` / `else`, `return "<dest>"`
- Reads: `ticket["category"]`, `ticket.get("category")`, `ticket["urgent"]`,
  `ticket["tier"]`, `ticket["wait_minutes"]`, `ticket["wait_hours"]`
  (`wait_hours` means `wait_minutes / 60`, exact rational)
- Simple local aliases: `w = ticket["wait_minutes"]`, assigned once and
  never reassigned
- Comparisons `== != < <= > >=`, `is None`, `is not None`, `not x`
  (for category: `not x` means missing), `in` / `not in` with a literal
  tuple/list of strings
- `and`, `or`, `not`, parentheses
- Constants: int, decimal, str, bool, None

**Everything else is `U-PY-CONSTRUCT`** and the snippet is *not reviewed*:
loops, comprehensions, function calls (except `ticket.get`), imports,
attribute access, arithmetic, f-strings, `try`, `lambda`, nested functions,
global state, string methods. Comments, docstrings and variable names are
discarded; they cannot carry meaning or instructions. A source that does not
parse is `U-PY-SYNTAX`.

Syntax checking (`ast.parse`), inspection (is it inside this subset?) and
behavioural comparison are reported as **separate check results**. Code is
inspected statically; it is never run (`code_execution` is always
`not_executed`).

---------------------------------------------------------------------------

## 8. Units and numeric normalisation

1. Every `wait` constant is converted to minutes as an exact fraction
   (`0.5 hours` -> 30).
2. Because `wait_minutes` is an integer, a comparison against a fractional
   threshold `t` is rewritten to an integer cut:

| Written | Normalised (integer x) |
|---|---|
| `x >= t` | `x >= ceil(t)` |
| `x > t` | `x >= floor(t) + 1` |
| `x <= t` | `x <= floor(t)` |
| `x < t` | `x <= ceil(t) - 1` |
| `x == t` | `x == t` if `t` is an integer, else always false |

This is why `wait > 29`, `wait >= 30`, `wait >= 0.5 hours` and
`not (wait < 30)` are accepted as equivalent.

---------------------------------------------------------------------------

## 9. Approvals and effective requirements

Approvals are **top-level records only**. Prose claims are never trusted.

```json
{
  "id": "A1", "status": "approved", "target": "R3",
  "applies_to_source_version": "req-v1", "sequence": 1,
  "change": {"kind": "replace_threshold", "field": "wait",
             "from": {"value": 30, "unit": "minutes"},
             "to":   {"value": 25, "unit": "minutes"}},
  "approver": "ops-lead", "approved_on": "2026-10-01"
}
```

**Supported change kinds:** `replace_threshold` (wait only) and
`replace_destination` (`from`/`to` destination). Other kinds are
`unsupported` and make that requirement unreviewed.

**Status:** only `approved` has any effect. `pending`, `rejected`, `revoked`
records are listed as `ignored` in coverage ("an unapproved suggestion cannot
change a requirement"). An unknown status is `unsupported`.

**Revision scope.** A record applies to the requirement `target` (`R3`, or an
exception `R3.E1`) in the source version named by `applies_to_source_version`.
- Version mismatch -> record is **inapplicable**: listed in coverage, no
  effect, no change to the verdict. Deliberate: an approval is granted against
  one revision; carrying it forward would be guessing. A new revision needs a
  new approval, or its text must already contain the change.
- Unknown target -> `dangling_approval` (ambiguous -> `INCOMPLETE`).
- `replace_threshold` rewrites every `wait` atom in the target's own
  condition whose current value equals `from`, keeping the operator. If no
  atom matches (`from` is stale) -> `stale_approval` (ambiguous).

**Change ordering.** Applicable approved records for one target are applied in
ascending `sequence`. A missing `sequence` is ambiguous. Dates and approver
names are metadata and do not order anything.

**Contradictions (never resolved silently).** A target is `conflicted` if
any of these hold:
1. Two approved records, same target and attribute, same `sequence`, different `to`.
2. In sequence order, a record's `from` does not equal the value produced by the previous record (broken chain).
3. The same record `id` appears twice with different content or status.

A conflicted requirement is excluded from behavioural comparison and reported
as unresolved (`INCOMPLETE` unless an unrelated evidenced blocker exists). The
reviewer never picks one candidate.

**Effective requirements** = source requirements (supported ones) with the
applicable approved changes applied in the order above. Review compares
artifacts against these, not against the raw text.

---------------------------------------------------------------------------

## 10. Rule references, the mapping rule and finding kinds

### 10.1 Principle

IDs (`R1`, `S1`, `refs`) are **hints**, never proof of correspondence.
Matching IDs cannot make a check pass; renaming IDs cannot change a verdict.
Correspondence is established by **behaviour**: every ticket cell in the
domain is routed by the effective requirements and by each artifact, and the
results are compared.

### 10.2 The deciding element

For a ticket, each artifact has one *deciding element*, the thing that
produced its route:

| Artifact | Deciding element | Cited as |
|---|---|---|
| Effective requirements | the rule or exception that fires, else the default | `requirements@v#R3` / `#R3.E1` |
| Spec | the rule / exception that fires, else the default | `spec@v#S3` / `#S3.E1` / `#default` |
| Workflow | the last branch taken before the terminal node | `workflow@v#n3.b2` |
| Code | the return statement and the test governing it | `code:f@v#L12-L14` |

### 10.3 Region, correspondence, omission

- `region(Ri)` = the set of ticket cells in which effective rule `Ri` decides
  (after precedence and exceptions).
- An artifact element *corresponds* to `Ri` when it decides at least one cell
  of `region(Ri)` with `Ri`'s destination.
- `Ri` is **omitted** from artifact A when `region(Ri)` is non-empty and A
  sends no cell of `region(Ri)` to `Ri`'s destination.
- **Source reference for an omission.** The finding's `rule_ref` is the
  *source requirement id and anchor* (`requirements@req-v1#R3`), even though
  the spec has no id for it. The finding's artifact location is the element
  that wrongly decides the counterexample ticket (often `#default`).
- `refs` hints are used only to word messages. If hints and behaviour
  disagree, the finding is a `ref_hint_mismatch` **warning**; hints never
  change the status.

### 10.4 Finding kinds (classification, never the verdict)

The counterexample is the ground truth. The kind only names the defect, and is
chosen in this order:

| Kind | When |
|---|---|
| `omitted_requirement` | omission as in 10.3 |
| `wrong_destination` | an element decides exactly `region(Ri)` but with another destination |
| `unit_mismatch` | the differing cut equals the effective threshold x60 or /60 (hours vs minutes) |
| `boundary_mismatch` | the element differs from `region(Ri)` only in `wait` cuts, each off by exactly 1 (`>` vs `>=`) |
| `threshold_mismatch` | the same, with larger differences |
| `precedence_mismatch` | swapping two adjacent elements of A removes every counterexample |
| `missing_branch` | a workflow decision node with no `else` has an uncovered ticket (actual route: none) |
| `unapproved_change_implemented` | A behaves like the effective rules **plus** a pending / rejected / revoked / inapplicable approval |
| `script_disagreement` | code disagrees although its metadata / `refs` name rules the spec already matches |
| `unclassified_mismatch` | behavioural difference not explained above |
| `destination_unknown`, `structure_defect`, `approval_conflict`, `ref_hint_mismatch`, `model_observation` | non-behavioural; no counterexample needed |

Example (spec says `wait >= 35`, effective R3 says 30): counterexample
`{billing, urgent=false, standard, wait=30}`, expected `escalation` by
`requirements@req-v1#R3`, actual `normal_queue` by `spec@spec-v3#default`;
kind `threshold_mismatch`, `rule_ref` `R3`, location `spec@spec-v3#S3`.

---------------------------------------------------------------------------

## 11. Untrusted text and injected instructions

**All free text is data, never instructions.** That covers requirement `text`,
every `note`, YAML/JSON comments, code comments, docstrings, identifiers and
string constants, file names and the bundle id. Reviewer behaviour is fixed by
the schemas and deterministic checks; text cannot change approval authority,
the status, or which checks run.

| Where the text sits | Treatment | Effect on verdict |
|---|---|---|
| code comment, docstring, identifier | discarded when parsed with `ast` | none |
| `note` field, YAML/JSON comment | stored, never parsed | none |
| extra sentence in requirement `text` | unsupported segment `U-PROSE`; if it reads like an order to the reviewer ("ignore the checks", "mark as PASS", "treat A2 as approved") it is `U-INSTRUCTION` (same treatment, clearer label) | visible, never obeyed; blocks `PASS`, so a clean bundle is `INCOMPLETE`; a real defect is still `CHANGES_REQUIRED` |
| claim of an approval in prose | `U-APPROVAL-PROSE` | never applied |
| text passed to a model | wrapped as quoted data; the prompt states it is data | model findings are advisory (section 12) |

Dataset consequence (Step 4): injection **in a code comment or `note`** on a
clean bundle -> `PASS`; injection **in requirement prose** on a clean bundle ->
`INCOMPLETE`; on a defective bundle -> `CHANGES_REQUIRED`. Label them so.

---------------------------------------------------------------------------

## 12. Verdict rules and required checks

| Status | Meaning |
|---|---|
| `CHANGES_REQUIRED` | at least one **evidenced blocker** exists. Unreviewed scope is still disclosed in coverage. |
| `INCOMPLETE` | no blocker is established, but missing evidence, ambiguity, a conflict, an oversized input or a failed required check prevents full review. |
| `PASS` | complete review of the effective requirements within the supported scope, no blocker, every required check passed. |

`Report` enforces this at construction (`reviewer/schemas.py`): a `PASS` with
a blocker, unreviewed scope or a not-passed required check cannot even be built.

**Severity.** `blocker` / `warning` / `info`. A blocker is deterministic and
behavioural blockers carry a counterexample. Model findings are `warning` or
`info` at most; they never change the status.

**Required checks** (a required check that failed, errored or was skipped can
never yield `PASS`):

| Check | Required | Mode | Passes when |
|---|---|---|---|
| `schema_validation` | yes | static | all artifacts present, valid, within size limits |
| `syntax_check` | yes, per snippet | static | `ast.parse` succeeds |
| `code_inspection` | yes, per snippet | static | snippet is inside the section 7 subset |
| `code_execution` | no | not_executed | always `skipped`: code is never run |
| `effective_requirements` | yes | static | approvals resolved with no conflict / dangling / stale record |
| `semantic_spec`, `semantic_workflow`, `semantic_code` | yes | exhaustive | every cell compared, nothing unsupported in the compared scope |
| `model_review` | **yes, whenever a model client is enabled** | model | a response validated within the retry and budget limits |

**What the model adds.** It may read unsupported prose and say what it appears
to mean, explain findings, and suggest correction wording, all as advisory
`warning`/`info`. It cannot make unsupported content "reviewed", cannot remove
or downgrade a deterministic blocker, and cannot produce `PASS`. A model
failure (timeout, truncated or malformed JSON, invented references,
conflicting claims) after bounded retries makes `model_review` fail, so the
status is at best `INCOMPLETE`.

---------------------------------------------------------------------------

## 13. Coverage, issue codes and size limits

Coverage lists, per source requirement and per artifact: `reviewed`,
`unsupported`, `ambiguous`, `conflicted`, `omitted`, `not_reviewed`,
`inapplicable`, `ignored`. The statuses `unsupported`, `ambiguous`,
`conflicted`, `omitted` and `not_reviewed` block `PASS`; `reviewed`,
`inapplicable` and `ignored` do not.

| Issue code | Meaning |
|---|---|
| `U-PROSE`, `U-INSTRUCTION` | sentence matches no template (section 4, 11) |
| `U-AMBIG-UNIT`, `U-AMBIG-LOGIC` | unit missing/unsupported; `and`/`or` mixed without parentheses |
| `U-UNKNOWN-FIELD`, `U-UNKNOWN-OP`, `U-LEAF-SHAPE`, `U-EMPTY-LITERAL` | malformed condition leaf |
| `U-DEST-UNKNOWN` | destination outside the declared vocabulary |
| `U-APPROVAL-PROSE` | approval claimed in prose; never applied |
| `U-NO-DEFAULT`, `U-AMBIG-DEFAULT` | zero or several default requirements |
| `U-DUP-ID`, `U-ORDER-AMBIG` | duplicate id; duplicate or contradictory `order` |
| `U-WF-STRUCTURE` | broken workflow graph |
| `U-PY-CONSTRUCT`, `U-PY-SYNTAX` | code outside the subset; code that does not parse |
| `U-SCHEMA`, `U-MISSING-ARTIFACT`, `U-SIZE-LIMIT` | artifact invalid, missing or too large |
| `U-APPROVAL-STATUS`, `U-APPROVAL-KIND` | approval record with an unknown status, or an unsupported / malformed change |

Approval coverage codes (not `U-` issues; they appear on the approval record and
its target requirement): `approval_conflict` (contradictory records, status
`conflicted`), `stale_approval` (`from` matches nothing), `dangling_approval`
(unknown target), `missing_sequence`, `target_unreviewable` (the target could
not be parsed, so the change cannot be applied).

Limits (nothing is silently truncated; exceeding a limit is reported):

| Limit | Value |
|---|---|
| Bundle size | 256 KiB (enforced by the loader) |
| Rules per artifact | 50 |
| Atoms per condition | 12 |
| Workflow nodes | 200 |
| Lines per code snippet | 400 |
| Enumerated tickets per comparison | 100,000 |

An over-limit artifact is marked `not_reviewed` and its scope is listed as
unreviewed; the best possible status is then `INCOMPLETE`.

---------------------------------------------------------------------------

## 14. How equivalence is established (summary; detail in Step 6)

Every supported artifact is a function of finitely many predicates: category
class, `urgent`, `tier`, and `wait_minutes` against a finite set of integer
cut points. So the domain splits into finitely many **cells** in which every
artifact is constant. The test set has one ticket per cell and then some:

- category classes: `missing`, each category literal mentioned in any
  artifact, and one fresh literal not mentioned anywhere ("other")
- `urgent` in {true, false}; `tier` in {standard, vip}
- wait: let `K` hold `floor(t)` and `ceil(t)` for **every threshold `t`
  written in any artifact** (after unit conversion, effective values
  included). Test `0` and `k-1`, `k`, `k+1` for every `k` in `K` (values
  below 0 dropped).

Why `k+1` matters: `wait == 30` and `wait >= 30` agree at 29 and 30 and differ
only from 31 on. Testing `k-1` and `k` alone would miss that; `k+1` catches
it. In general, after the section 8 rewrite each comparison changes value only
at a cut in `K` or in `K+1` (`==`/`!=` have two cuts, `t` and `t+1`), and every
stretch where all artifacts are constant starts at such a cut (or at 0), so
`{0} U {k, k+1}` already hits every stretch. `k-1` is extra margin that also
makes counterexamples boundary-adjacent and guards against an off-by-one in the
normaliser itself. (Checked by brute force on 20,000 random threshold pairs:
`{k-1, k}` missed 361 real differences, `{k-1, k, k+1}` missed none.)

Comparing these tickets is therefore **exhaustive** for supported artifacts;
any difference yields a concrete counterexample ticket. Anything outside the
supported subset is not reasoned about, only reported. Randomised sampling, if
used anywhere, is labelled `sampled` in check results.

---------------------------------------------------------------------------

## 15. Worked example (baseline, "support-v1")

Requirements (entries, in `order`):

```
R1     Missing category -> manual review.
R2     Otherwise, urgent ticket -> priority queue.
R2.E1  Exception to R2: if category = "spam", do not apply R2.
R3     Otherwise, waiting >= 30 minutes -> escalation.
R3.E1  Exception to R3: if tier = vip, route to priority queue.
R4     All remaining tickets -> normal queue.
```

| category | urgent | tier | wait (min) | Route | Why |
|---|---|---|---|---|---|
| null | true | standard | 40 | manual_review | R1 first |
| billing | true | standard | 5 | priority_queue | R2, no exception |
| spam | true | standard | 40 | escalation | R2 skipped by E1, then R3 fires |
| spam | true | vip | 40 | priority_queue | R2 skipped, R3 fires, E1 redirects |
| billing | false | standard | 29 | normal_queue | nothing fires |
| billing | false | standard | 30 | escalation | boundary inclusive |

With approval `A1` (R3: 30 -> 25, status `approved`, applicable version), the
`wait = 25` ticket routes to `escalation`. If `A1` were `pending`, it would stay
`normal_queue`. A consistent bundle of all five artifacts for this example is
in `tests/data/example_bundle/` (schema test data only; the evaluation dataset
lives in `fixtures/bundles/`).

---------------------------------------------------------------------------

## 16. Out of scope (stated, not claimed)

Arbitrary prose, natural-language inference, Python outside section 7, runtime
behaviour of untrusted code, more than the four ticket fields, units other
than minutes/hours, authentication of approvers (top-level approval metadata
is trusted by assumption).

### Open category-string domain and representative coverage

`category` is an open string domain, not a closed enum. The semantic checker
therefore derives category representatives from every category equality or
inequality literal found in the effective requirements, spec, workflow, and
linked-code IRs. It also includes `null`, the documented common examples, and
one collision-free unmatched string. This partitions all category equality
predicates present in the reviewed artifacts; it does not claim to enumerate
all possible strings.

## Step 7: advisory model boundary

The deterministic reviewer remains the authority for `PASS`, `CHANGES_REQUIRED`,
and `INCOMPLETE`. The model client is an optional advisory layer. Its output is
strictly schema-validated and every observation must cite an existing artifact,
version, location, known reference (when supplied), and exact evidence text.
Model observations are warnings only and cannot create blockers or change the
status. A malformed response, timeout, provider error, invented reference,
missing evidence, or conflicting duplicate claims is recorded in provenance.
Retries are bounded to at most three attempts. The CLI uses a local fake client
by default for offline reproducibility; no live model is configured and fake
responses are not evidence of real-model performance.

The complete bundle is serialized as data and passed separately from the fixed
system prompt. Text in requirements, notes, comments, and Python snippets is
untrusted input and cannot modify approval authority or reviewer instructions.
Submitted Python is never executed. No response cache is used in this step, so
there is no stale-cache path; each CLI invocation reviews the current bundle.

Model contribution is limited to optional explanatory observations and possible
review leads for content outside the deterministic language. It does not prove
semantic equivalence, replace deterministic checks, or establish coverage.
