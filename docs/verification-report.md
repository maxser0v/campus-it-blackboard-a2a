# Independent verification report

**Date:** 7 November 2026  
**Scope:** Integration and acceptance checks against [BLACKBOARD_PROJECT_PLAN.md](../BLACKBOARD_PROJECT_PLAN.md). The service runs below used isolated loopback ports, temporary SQLite databases, generated local credentials, and the explicit offline fixture adapter. No external model API or paid provider was contacted.

## Latest bugfix verification

The final frozen source passes **108 tests in 13.58 seconds**. A fresh isolated real A2A evaluation ran each fixture three times: database **3/3 completed** in nine dispatches, DNS **3/3 completed** in five, and missing evidence **3/3 unresolved** in six. All strict missing-evidence gates passed. The nine cases totalled 7.493 seconds; all 434 audit outcomes were `ok` and all 60 specialist finishes recorded `TASK_STATE_COMPLETED`. See [final evaluator JSON](../evaluation/results/evaluation-20261107T070922Z.json) and [bugfix verification](bugfix-verification.md) for regression coverage and the two preserved intermediate failures corrected during integration verification.

The results below are retained as the earlier 6 November baseline. Current missing-evidence runs end at board revision 22 with one review; the older ten-dispatch/three-review trace is historical. Default services were not restarted for the bugfix verification.

## Commands and results

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | **61 passed** in 6.42 seconds |
| `.venv/bin/python -m pytest -q tests/unit/test_service_scripts.py` | **7 passed** in 0.02 seconds |
| `scripts/evaluate.py --repeat 3 --poll-seconds 0.1 --wait-seconds 60` | **9 case runs** in 9.001 seconds; result: [evaluation-20261106T181622Z.json](../evaluation/results/evaluation-20261106T181622Z.json) |

The acceptance test starts the Blackboard, four specialist A2A services, and controller as separate Uvicorn processes. It reads every Agent Card, checks the JSON-RPC binding, makes authenticated Blackboard calls, runs the three cases, then terminates only those processes. Its generated database and tokens remain in the test temporary directory. For the persisted evaluator run, `scripts/start.py` correctly refused to start because another seven-process project stack was already active. To leave those processes untouched, the evaluator was run against a separate set of direct Uvicorn processes on temporary ports and a temporary database; `scripts/evaluate.py` wrote the linked result JSON.

## Findings

- **A2A discovery and task flow:** All five Agent Cards were discoverable over HTTP. An unauthenticated JSON-RPC request returned HTTP 401. Every finished specialist dispatch recorded a task ID, context ID, and `TASK_STATE_COMPLETED` status. The controller retained distinct problem, dispatch, task, and context identifiers.
- **Database case:** All three evaluator runs completed in 9 specialist dispatches. Each retained two hypotheses, revised both from revision 1 to revision 2, fulfilled one targeted pool query, accepted the current supported hypothesis, and reviewed the response at its current revision. Each board ended at revision 32. The expected-status, evidence-reference, current-review, response-reference, runbook, request-link, and dispatch-limit checks passed on all three runs.
- **DNS case:** All three evaluator runs completed in 5 dispatches, with one supported hypothesis, two reviews, and one solution; each board ended at revision 18. The accepted name-resolution hypothesis cited the fixture's NXDOMAIN metric, and the response passed review. The evaluator's “requested evidence was linked” check is false for these runs because the initial DNS measurement was sufficient and no follow-up was requested.
- **Insufficient-evidence case:** All three evaluator runs ended `unresolved` in 10 dispatches, with two observations, three hypotheses, three reviews, one unique unavailable evidence request, and no solution; each board ended at revision 32. The missing-evidence-specific check passed, and no supported cause was accepted. The status action keeps unavailable evidence distinct from fulfilled evidence.
- **SQLite behavior:** Independent checks confirmed state survives reopening the database, the authenticated actor determines `source_agent` despite a spoofed payload field, a repeated request ID creates one entry, a changed payload with that ID is rejected, and two concurrent updates to the same expected revision produce one success and one `revision_conflict`. Concurrent read/status calls and writes preserved consistent snapshots without storage errors. Reads do not increment the board revision.
- **Model adapter:** A local fake OpenAI-compatible HTTP endpoint verified the configured output-token field and cap, timeout, API key header, and provider usage values. Malformed content, HTTP 503, and a timed-out response all surface as `ModelAdapterError`; no fallback finding or fabricated usage is produced. The live adapter remains configurable through `CAMPUS_MODEL_BASE_URL`, `CAMPUS_MODEL_NAME`, `CAMPUS_MODEL_API_KEY`, `CAMPUS_MODEL_TIMEOUT_SECONDS`, `CAMPUS_MODEL_MAX_OUTPUT_TOKENS`, and `CAMPUS_MODEL_TOKEN_LIMIT_PARAMETER`.
- **Evidence and answer keys:** Agent tools read only the case fixture and runbook roots. The case summary passed to agents omits metric and log records; tool results include provenance IDs. `evaluation/answer_keys/` is only referenced by `scripts/evaluate.py`, not by agent tools or role runners. The completed Blackboard entries contained no answer-key field.
- **Launcher safety:** Seven focused regressions simulate `ps` permission/ambiguous failures, an occupied service port, and a child that exits after all health URLs respond. They verify that start/stop preserve the PID journal when inspection fails, port conflicts are detected before child launch or journal replacement, and HTTP readiness from an old listener cannot make an exited new child appear started. The tests use fake process/socket probes and did not inspect or signal the tracked default stack.
- **Evaluator caveat:** The evaluator's literal ground-truth label string check was false in all nine runs, because the offline diagnoses paraphrased those labels. This is a rough text check, not a reasoning metric; inspect the cited records and claims manually. The missing-evidence run is expected to have false “accepted cause” and response checks because it correctly produced no accepted cause or solution.

## Defect found and corrected

The first real concurrent run intermittently returned a Blackboard `storage_error` on a read. The read path held a SQLite WAL snapshot open while inserting its audit row, which can fail when another writer commits before that transaction is upgraded. `read_blackboard` and `get_problem_status` now finish their consistent read snapshots before writing their audit rows. The final persisted evaluation recorded **zero storage-error audits**. A missing-evidence trace also reissued the same unavailable metric query under new request IDs; equivalent queries are now suppressed and the controller reaches an unresolved result after one unavailable request.

The launcher audit found that treating a failed `ps` probe as a missing PID could erase the process journal and allow an existing service to answer the new child's health check. Process inspection errors now abort safely with the journal preserved; startup preflights ports and confirms each child is still alive after readiness checks. The seven new regressions and final full suite pass.

## Limits

This verifies local A2A interoperability and a fake OpenAI-compatible endpoint, not compatibility with a paid external provider. The evaluation ran each fixture case three times. Offline runs do not provide provider token-usage measurements, so live provider usage and the cumulative run-token ceiling were not exercised. These checks do not establish a statistical success rate or prove that a real-world incident would be diagnosed correctly. The optional browser/dashboard screenshot check was not part of this run.
