# Material AI Assistance

AI assistance contributed to planning and directly edited evidence attribution, model citation validation, the oracle interpreter, evaluation, tests and documentation. It did not serve as the independent oracle or as the source of expected labels.

- **Planning and decomposition:** helped break the take-home requirements into staged work, identify contracts and trust boundaries, and organize the repository. The project checks the resulting plan against the assessment requirements and repository behavior.
- **Code review and debugging:** helped inspect parser, approval-resolution, semantic-comparison, status and model-failure paths; suggested edge cases and regression tests. The verification log records the tests executed by Codex.
- **Test/evaluation support:** helped design test cases for threshold boundaries, arbitrary category values, missing workflow branches, approval conflicts, malformed model output, timeouts and invented evidence. The independent oracle and label checker are separate project code; AI assistance did not replace their execution.
- **Documentation:** helped draft the engineering note, evaluation explanation and demo outline. The project checks numerical claims against the generated evaluation report and test output.

**Verification performed:** `python -m pytest -q` (218 passed); `python -m oracle.check_labels` (23 labels consistent with the brute-force oracle); and `python -m eval.run_eval --client fake --out reports/final.json` (23/23 expected statuses, including 5/5 held-out cases). Model integration uses a scripted fake client only; no live LLM result is represented as measured evidence.

AI-generated suggestions were treated as proposals, not trusted facts. The final repository, claims, test execution, and submission should be reviewed and understood by the author before delivery.
