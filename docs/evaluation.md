# Evaluation record

## Measurement method

Run `python scripts/evaluate.py` against the locally started services. The script starts a fresh problem for each selected fixture case, waits for a terminal state, reads the Blackboard through the read-only A2A identity, and writes one JSON record under `evaluation/results/`.

The record includes model mode, final status and reason, elapsed time, specialist dispatch count, entry counts, and checks for exact-revision review gates, evidence references, runbook references, requested-evidence links, and the case’s expected terminal behavior. `--repeat 3 --case db_pool` creates three fresh main-case runs. When the configured model mode is `live`, the script refuses to call the provider unless `--allow-live` is passed.

The answer-key label check is only a rough string match. It does not establish that the reasoning is correct. Review the underlying evidence, hypothesis changes, and response manually. Report every run, including failures; do not summarize these prototype runs as a statistically supported success rate.

## Results

### Final isolated offline evaluation

Result file: [evaluation-20261106T181622Z.json](../evaluation/results/evaluation-20261106T181622Z.json). The evaluator ran all three cases three times each against separate local services, temporary ports, generated credentials, and a temporary SQLite database. It used offline mode and made no provider calls. The nine evaluations took 9.001 seconds in total.

| Case | Repetitions and outcome | Dispatches | Board revision | Final entries per run (observations / hypotheses / requests / reviews / solutions) | Checks and notes |
| --- | --- | ---: | ---: | --- | --- |
| `db_pool` | 3/3 completed | 9 each | 32 each | 2 / 2 / 1 / 3 / 1 | Expected terminal result, valid evidence references, accepted current hypothesis, current response review, response references, runbook, fulfilled request link, and dispatch bound all passed. Both hypotheses have revision history 1→2. |
| `dns_fault` | 3/3 completed | 5 each | 18 each | 1 / 1 / 0 / 2 / 1 | Expected terminal result, valid references, accepted current hypothesis, current response review, response references, runbook, and dispatch bound passed. No follow-up request was needed. |
| `missing_evidence` | 3/3 unresolved | 10 each | 32 each | 2 / 3 / 1 / 3 / 0 | Expected unresolved result, valid hypothesis references, no accepted cause, unique unavailable request, and dispatch bound passed. The three hypotheses remain at revision 1. |

The literal ground-truth label check was false for all nine runs because the offline diagnoses paraphrased the labels; the label check is not a reasoning score. `requested_evidence_was_linked` was false for DNS because no request was needed, and for missing evidence because its one linked query returned no records. The missing-evidence case correctly had no accepted supported cause, reviewed response, response references, or runbook proposal. These false checks are expected for that case.

An earlier shared-stack attempt encountered a transient 503 while reading during concurrent writes and produced no result JSON. The SQLite read/audit snapshot race was corrected before the isolated nine-run evaluation; the final run recorded zero storage-error audits. The start script also correctly declined to start over the already-running shared stack, so the final evaluator used only its own direct Uvicorn processes and did not stop or write to that stack.

The separate test suite verifies A2A IDs, authentication, revisions, idempotency, concurrency, and a local fake provider. It does not establish compatibility with an external model provider. Offline mode reports no provider token counts, so live usage and the cumulative run-token ceiling were not exercised. Manual review of each cited claim and observation remains necessary.

The final locked suite after adding launcher safety regressions passed **61 tests in 6.42 seconds**. The seven new script tests verify process-inspection failures preserve the PID journal, occupied ports block launch before state changes, and a successful old-server health response does not mask an exited new service process. These tests use simulated process/socket probes and do not stop or signal the tracked default stack.
