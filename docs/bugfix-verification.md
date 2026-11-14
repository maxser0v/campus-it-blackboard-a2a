# Independent bugfix verification

Date: 7 November 2026. Verification uses offline fixtures, isolated loopback services, temporary SQLite databases and generated local credentials. No paid model calls are made and the default service stack is left untouched.

## Regression coverage

| Defect | Meaningful regression coverage |
| --- | --- |
| Observation revisions ignored | Scheduler tests change an existing observation revision after accepted diagnosis; legacy hypotheses without version provenance require reanalysis. Diagnosis tests ensure provenance comes from the actual snapshot rather than model-supplied references. |
| Completion at dispatch/token boundary | Scheduler completion test plus complete conditional workflows at dispatch/token limits `(12,12000)`, `(9,12000)`, `(12,9)`, `(9,9)`. Existing limit tests still stop further work. |
| Slow board operation passes deadline | Initial snapshot, later snapshot and dispatch-decision write stalls are exercised; no specialist starts after deadline. |
| Repeated evidence dispatch changes payload | Restart/lost acknowledgement test changes the tool clock and prohibits model/tool replay; concurrent deliveries force both initial cache misses and require one canonical entry/result. |
| Invalid tool arguments bypass repair | Invalid types, lengths, bounds, Boolean limits and log levels trigger one model repair; repeated invalid evidence requests leave no stored entries; direct fixture calls reject invalid arguments. |
| Operational failure counted as missing-evidence success | Evaluator rejects blocked runs, invalid stop reasons, broken request/query links, nonempty unavailable records, accepted cause and solution; real A2A acceptance requires the strict evaluator gates. |

## Verification findings

The initial frozen full suite passed **99 tests in 10.93 seconds** with loopback access. The first restricted run could not bind local sockets (94 passed, three failures and one setup error); that environment restriction was resolved by running the local-service checks with approved loopback access.

The independent real evaluator then exposed two interactions not covered by that suite: stale prior hypotheses caused the missing-evidence case to stop after four dispatches, and raw query equality rejected the fixture tool's explicit `contains: null` default. That run is preserved as [evaluation-20261107T070610Z.json](../evaluation/results/evaluation-20261107T070610Z.json). The database and DNS cases completed in nine and five dispatches respectively. `test_real_missing_evidence_satisfies_strict_evaluator` was added independently and reproduced the missing-evidence failure. A second provisional run, [evaluation-20261107T070840Z.json](../evaluation/results/evaluation-20261107T070840Z.json), then exposed a stale-first specialist review selection mismatch. The scheduler and specialist now agree on current evidence context; the final checks below pass.

The archive-to-workspace review found that exact current hypothesis/solution review gates and evidence/runbook gates remain enforced. Evidence replay checks actor, problem, case, role, purpose and dispatch identity and reads committed history. Deadline operations use a lazy operation factory and cancellation-aware timeout; cancellation is not swallowed. Terminal bookkeeping has a separate two-second allowance and cannot launch specialist work. A client timeout does not prove a remote A2A server stopped executing, so remote cancellation remains outside these checks.

## Final frozen results

- `.venv/bin/python -m pytest -q`: **108 passed in 13.58 seconds**, including the new independent real A2A evaluator gate.
- `PYTHONPATH=src .venv/bin/python /private/tmp/verify_bugfix_evaluation.py`: isolated six-service harness invoking `scripts/evaluate.py --repeat 3 --poll-seconds 0.1 --wait-seconds 60`; [final JSON](../evaluation/results/evaluation-20261107T070922Z.json). The temporary harness reused the acceptance fixture's launch/cleanup code and left default services untouched.
- Database: **3/3 completed**, nine dispatches, board revision 32, two observations, two hypotheses, one request, three reviews and one solution each.
- DNS: **3/3 completed**, five dispatches, board revision 18, one observation, one hypothesis, zero requests, two reviews and one solution each.
- Missing evidence: **3/3 unresolved**, six dispatches, board revision 22, two observations, three hypotheses, one unavailable request, one review and zero solutions each. Every strict missing-evidence check passed; each stopped for `hypotheses_rejected_without_new_evidence`.
- Total measured case execution: **7.493 seconds**. All **434 audit events** had outcome `ok`; all **60 specialist dispatches** recorded `TASK_STATE_COMPLETED`.

The literal ground-truth-label check remains false for all nine paraphrased diagnoses. DNS needs no follow-up request. Missing-evidence cases have no accepted supported cause, response or fulfilled request, so those positive checks remain false as expected. These are fixture checks, not measured live-model performance. Synthetic provider usage exercises cumulative token boundary behavior; offline A2A runs report provider usage as unmeasured.
