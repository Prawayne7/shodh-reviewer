# shodh-reviewer

An AI-assisted reviewer that checks whether a structured specification, a decision workflow and linked Python snippets implement written support-ticket routing requirements. Deterministic semantic checks decide the verdict; a configured model check is advisory and cannot override deterministic blockers. The included fake client exercises the model boundary and failure handling offline. Python 3.11 or 3.12, CPU only. Verified on Windows with Python 3.11.9.

## Run it

```bash
python -m pip install -r requirements.txt
python -X utf8 verify_local.py
```

Or use `make eval` to create a virtual environment, install pinned dependencies, run tests and execute the evaluation. `make test` runs only tests; `make clean` removes the virtual environment and caches.

## Current verification protocol

- **Tests:** 218 passed.
- **Independent label check:** 23 labels consistent with the brute-force oracle.
- **Evaluation:** 23/23 correct statuses on the synthetic set; 5/5 on the held-out split; zero false passes and zero false blocks.
- **Dataset:** 7 valid, 11 defective and 5 incomplete bundles.
- **Model:** scripted fake client only; no live provider is configured and no real-model performance is claimed.
- **Finding quality:** 14/14 matched the labeled artifact and defect location, with 14/14 correct rule references and 13/13 independently replayed routing counterexamples.

These metrics apply only to this small synthetic support-routing policy family. See `docs/step8_evaluation.md` and `docs/dataset.md` for the protocol, split, limitations and development-evidence disclosure.

## Pipeline

1. Validate bundle contracts and artifact versions.
2. Parse requirements, specification, workflow and linked Python into a common representation. Python is parsed with `ast`, never executed.
3. Resolve effective requirements from applicable accepted approvals.
4. Compare semantics across supported artifacts and generate evidence-backed findings and counterexamples.
5. Apply centralized verdict precedence: `PASS`, `CHANGES_REQUIRED` or `INCOMPLETE`.
6. If configured, run a bounded, schema- and evidence-validated advisory model review. A failed required model check results in `INCOMPLETE` unless a deterministic blocker already requires `CHANGES_REQUIRED`.

## Try individual bundles

```bash
python -X utf8 demo_local.py
python -m reviewer.cli fixtures/bundles/b14 --out reports/b14.json --model fake
```

Use `--model off` to run the deterministic reviewer without the model layer. The fake model scripts are test fixtures, not a substitute for a live provider.

## Repository map

```text
reviewer/              schemas, loader, parsers, IR, approvals, semantics, status, reports, model boundary
fixtures/bundles/      23 input bundles (b01 ... b23)
fixtures/model_scripts/ scripted fake-model responses and failure cases
labels/                expected outcomes; scoring only, never reviewer input
oracle/                independent policy/interpreters and label consistency checker
eval/                  evaluation harness and metrics
reports/               recorded JSON evaluation and example reports
tests/                 unit, integration and regression tests
docs/                  supported language, dataset, evaluation, engineering note,
                       AI assistance note, demo script and submission checklist
```

See `docs/supported_language.md` for the input contracts and supported subset. Historical held-out freeze timing and tuning isolation are unverified.

## Key trust and correctness rules

- IDs and `refs` are hints; correspondence is established by behavior.
- Array position is never semantic; ordered collections use explicit `order`.
- Free text, notes and code comments are data, never instructions.
- Unsupported or ambiguous content stays visible in coverage and cannot lead to `PASS`.
- Code is parsed with `ast` and never executed.
- Model observations are advisory, require valid evidence and cannot override deterministic status.

## Submission documents

- `docs/engineering_note.pdf` — rendered engineering note.
- `docs/ai_assistance.pdf` — rendered AI assistance disclosure.
- `docs/demo_script.md` — narration and terminal sequence for an actual two-minute recording.
- `docs/submission_checklist.md` — final verification and applicant-owned external submission steps.
