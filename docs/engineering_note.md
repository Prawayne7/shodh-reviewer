# Shodh AI — Engineering Note

## 1. Problem and scope

This project implements an AI-assisted reviewer for support-ticket routing policy bundles. A bundle contains written requirements, a structured specification, a workflow graph, linked Python snippets, and approval records. The reviewer determines whether the implementation artifacts agree with the effective approved policy and returns one of three outcomes: `PASS`, `CHANGES_REQUIRED`, or `INCOMPLETE`.

The supported language is deliberately bounded and documented in `docs/supported_language.md`. Verification is exhaustive over representatives of the supported ticket partitions defined there and only for conditions and constructs in the supported subset. It is not a proof of arbitrary natural-language understanding or arbitrary Python equivalence. Unsupported or ambiguous material is surfaced as coverage and cannot be silently treated as verified.

## 2. Architecture and trust boundaries

The pipeline is split into explicit stages:

1. **Load and validate.** `reviewer/loader.py` loads a bundle and Pydantic contracts in `reviewer/schemas.py` validate the artifact shapes. Missing or unknown structures remain visible as unreviewed coverage.
2. **Parse into a common representation.** Requirement prose, YAML specification rules, workflow nodes/branches and linked Python snippets are translated into a shared rule representation. Python is parsed with `ast`; snippets are inspected, never executed.
3. **Resolve effective requirements.** `reviewer/effective.py` evaluates approval records. Only applicable, accepted approvals change the effective policy. Pending suggestions and free-text claims are not approval authority. Conflicting approvals prevent a confident verdict.
4. **Compare semantics.** `reviewer/semantics.py` compares the effective requirements against the spec, workflow and code. It evaluates representative ticket inputs across supported condition boundaries and emits findings with evidence locations and counterexamples.
5. **Decide status.** `reviewer/status.py` centralizes status precedence. A confirmed deterministic disagreement yields `CHANGES_REQUIRED`; missing, ambiguous or unsupported coverage yields `INCOMPLETE`; `PASS` is available only when required checks complete without a disagreement or unresolved coverage.
6. **Optional configured model review.** `reviewer/model_review.py` calls a provider through the `ModelClient` contract. The current runnable integration is a deterministic fake client; no live model provider is configured. Model output is advisory, schema-checked and evidence-checked. It cannot override a deterministic blocker or create a deterministic blocker. If a configured required model check fails after bounded retries, the result is `INCOMPLETE` unless a deterministic blocker already requires `CHANGES_REQUIRED`.

All free text from requirements, notes, comments and approvals is treated as untrusted data, not executable instructions. Model responses must use known rule references, evidence locations and exact quotations from the bundle. Requests and responses are hashed in provenance; failures and retries are recorded. Retries are bounded to avoid unbounded calls.

## 3. Semantic comparison and evidence

The reviewer does not rely on matching IDs alone. IDs and `refs` are hints; correspondence is established by behavior. Explicit `order` fields, rather than serialized array positions, define ordered semantics so harmless record reordering does not change meaning. Approval resolution is performed before semantic comparison, ensuring the comparison target is the effective approved policy rather than a pending proposal or stale base rule.

For supported predicates, the checker partitions numeric wait-time conditions at each threshold and evaluates representatives from the resulting truth-value regions. It also evaluates relevant category, urgency and tier combinations, including unmatched category representatives. This makes boundary changes such as `>= 30` versus `> 30`, unit changes such as minutes versus hours, missing branches and precedence interactions observable in the finite domain. A counterexample records a concrete ticket and the evidence location responsible for the observed decision.

Evidence uses artifact, version and location coordinates. Locations derive from stable IDs and explicit order values, not array indexes. The JSON report includes findings, coverage, check results, counterexamples and model-call provenance. This makes the verdict inspectable rather than a bare model-generated conclusion.

## 4. Dataset and evaluation

The evaluation set contains 23 synthetic bundles: 7 valid, 11 defective and 5 incomplete. Five are designated held-out (one valid, three defective and one incomplete); b19 was moved to development after its timeout behavior influenced Stage-3 tuning. The dataset includes approval changes and conflicts, missing requirements, threshold boundaries, units, precedence, missing workflow branches, code disagreements, injected instructions, multi-rule interactions and model failure cases. The dataset and limitations are described in `docs/dataset.md`.

Labels stay outside reviewer inputs. The evaluator uses them for scoring and selecting configured synthetic failure scripts. An independent oracle in `oracle/` uses a separately written policy and interpreters to check the labels against a dense brute-force ticket grid. `python -m oracle.check_labels` verifies label consistency. The reviewer does not import the oracle or read labels.

The local configured fake-client run matched 23/23 statuses: 7/7 valid, 11/11 defective and 5/5 incomplete. False blocks were 0/7 and false passes were 0/11. The designated held-out split matched 5/5. Under the final scorer, 14/14 findings matched the labeled artifact and defect location, 14/14 cited the expected rule, and 13/13 routing counterexamples were independently replayed. The approval conflict is the fourteenth finding and has no route witness. The supplied full reviewer scored 13/14 on its original, looser location scorer and had three matched rule-reference errors. The Step 6 checkpoint and model-disabled ablation both scored 22/23 because b19 passed without the required configured model check.

The ZIP contains no Git history. The original manifest's five held-out labels, bundle hashes, seed, generator and oracle policy match their recorded digests. The timing of that freeze and isolation from historical tuning remain unverified. The prospective manifest records the implementation and checkpoint digests before the final local rerun. It is a reproducibility snapshot of the supplied split. The five designated cases do not establish blind holdout performance.

## 5. Debugging evidence

Both regressions below were reproduced on the supplied ZIP. The actual failing and passing test output is preserved in reports/debug/attribution-before.txt and attribution-after.txt.

**Unit defect localized to the fallback (b14).** The failing assertion showed default instead of S3. The deciding default described the route taken, while the threshold rule caused the divergence. Varying only wait time finds a corroborating decision by S3 while the source decision stays fixed. The regression asserts S3, the R3 reference and the preserved fallback trace.

**Early threshold attributed to the wrong source (b08).** Three failing assertions showed R4 instead of R3 across spec, workflow and code. R4 was the correct decision at the original witness, but the source threshold was responsible for the disagreement. A corroborating wait-only probe establishes the unique responsible source rule. The parameterized regression checks all three artifacts and retains R4 in the original trace.

## 6. Limitations and next steps

The dataset is small, synthetic and generated around one support-routing policy family. Historical tuning isolation for the designated five-case split is unverified. The finite-domain guarantee applies only to the documented subset. AST inspection does not execute arbitrary Python and therefore cannot establish behavior for unsupported constructs, dynamic imports, external state or arbitrary side effects. The fake client does not establish real LLM quality or robustness. The next engineering steps would be a provider-backed client with secure secrets management, larger independently authored policy families, external adjudication of labels, mutation testing, and latency/cost measurement under a documented deployment setup.

## Reproduction

From the repository root, with Python 3.11 or 3.12:

```bash
pip install -r requirements.txt
python -X utf8 verify_local.py
python -X utf8 demo_local.py
```

The expected current checks are 218 passing tests, a successful oracle check for 23 labels, and a 23/23 final fake-client evaluation. See `README.md` for the CLI and repository map.
