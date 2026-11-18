# Evaluation and debugging

Run `python -X utf8 verify_local.py` with pinned dependencies installed. This runs tests, verifies both manifests, independently checks labels, regenerates the original dataset in a scratch directory and reruns checkpoint, final and ablation on all 23 bundles. The Windows regeneration check normalizes line endings for comparison. The freeze check uses the original bytes.

The local configured fake-client run matched 23/23 statuses: 7/7 valid, 11/11 defective and 5/5 incomplete. False blocks were 0/7 and false passes were 0/11. The designated held-out split matched 5/5. Under the final scorer, 14/14 findings matched the labeled artifact and defect location, 14/14 cited the expected rule, and 13/13 routing counterexamples were independently replayed. The approval conflict is the fourteenth finding and has no route witness. The supplied full reviewer scored 13/14 on its original, looser location scorer and had three matched rule-reference errors. The Step 6 checkpoint and model-disabled ablation both scored 22/23 because b19 passed without the required configured model check.

| Run | Status matches | Valid false blocks | Defective false passes | Incomplete abstentions | Wall time | Calls | Retries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Preserved Step 6 | 22/23 | 0/7 | 0/11 | 4/5 | 1.3117 s | 0 | 0 |
| Corrected, model disabled | 22/23 | 0/7 | 0/11 | 4/5 | 0.2294 s | 0 | 0 |
| Corrected, fake client | 23/23 | 0/7 | 0/11 | 5/5 | 0.2348 s | 25 | 2 |

Checkpoint timing includes process startup and I/O. Final and ablation review times exclude dataset loading and scoring. Total evaluation wall time was 2.2806 s. Raw counts by split and per-case evidence are in reports/final.json. There were 0/14 unmatched final findings. Finding scoring matches one-to-one on artifact and primary location, replays routing witnesses against the independent oracle, and scores rule references separately. The earlier supplied reviewer used a looser score. Checkpoint findings were not scored by its supplied status-only runner.

## Freeze provenance and scope

The ZIP contains no Git history. The original manifest's five held-out labels, bundle hashes, seed, generator and oracle policy match their recorded digests. The timing of that freeze and isolation from historical tuning remain unverified. The prospective manifest records the implementation and checkpoint digests before the final local rerun. It is a reproducibility snapshot of the supplied split. The five designated cases do not establish blind holdout performance.

The 23 cases are synthetic and share one policy family. The developer cases b08 and b14 were used for the correction. Fake responses exercise the model interface and failure handling. Live-model performance was not measured. The model-disabled ablation changes only b19. Model outputs cannot create deterministic blockers. The Python contract is restricted and the oracle runs a separate AST interpreter.

## Debugging evidence

Actual before and after test logs are under reports/debug. b14 initially asserted default versus S3. The three b08 assertions initially showed R4 versus R3. The checker now uses a unique corroborating wait-only probe and preserves both original route-decision locations in the counterexample. See tests/test_evidence_attribution.py for the regressions and docs/engineering_note.md for the diagnosis.
