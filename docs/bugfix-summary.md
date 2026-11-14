# Bug-fix verification summary

**Date:** 7 November 2026  
**Scope:** Six correctness fixes in the offline classroom prototype. No live model provider was selected or contacted.

| Defect | Corrected behavior | Regression evidence |
| --- | --- | --- |
| An observation changed but an accepted diagnosis was still treated as current. | Diagnosis provenance records the actual observation revisions. The scheduler requires reanalysis when those revisions change, including legacy hypotheses without revision provenance. Only hypotheses from the fresh diagnostic pass authorize review, response, or completion. | `test_changed_observation_invalidates_existing_accepted_diagnosis`, `test_legacy_hypothesis_without_evidence_versions_requires_reanalysis`, `test_diagnosis_records_actual_observation_revisions_not_model_refs`, `test_fresh_unresolved_diagnosis_does_not_reuse_stale_supported_cause`, `test_real_missing_evidence_fixture_reaches_unavailable_result` |
| A valid final review at the exact dispatch or reported-token limit ended unresolved. | The controller checks whether the current board has satisfied completion before enforcing a limit on further work. | `test_completed_board_can_finish_at_dispatch_limit`, parametrized `test_controller_runs_conditional_evidence_loop_to_reviewed_solution` |
| A slow Blackboard operation could consume the deadline and still allow a specialist to start. | The controller bounds case work by the remaining deadline and checks again after board operations and before specialist dispatch. Terminal bookkeeping has a two-second allowance. | `test_deadline_during_board_operation_never_starts_specialist` |
| Repeated or concurrent evidence delivery could conflict instead of replaying a persisted result. | A replay returns the canonical persisted observation, including after restart or a lost acknowledgement. Concurrent delivery produces one canonical result. | `test_evidence_dispatch_replays_persisted_result_after_restart_and_lost_ack`, `test_concurrent_evidence_deliveries_return_one_canonical_result` |
| Malformed tool arguments escaped the model-output repair step or persisted unusable requests. | Tool names and arguments are validated before execution or request storage. Invalid output receives the existing bounded repair attempt. | `test_invalid_tool_arguments_are_repaired_before_execution`, `test_invalid_evidence_request_is_not_persisted` |
| An operationally blocked missing-evidence run could pass evaluation. | Missing-evidence success requires an evidence-limited unresolved stop with an actual unavailable request, no accepted cause, and no solution. A blocked operational failure fails the case check. | `test_missing_evidence_blocked_without_evidence_is_operational_failure`, `test_missing_evidence_requires_actual_evidence_limited_stop` |

## Final verification

The frozen full suite passed **108 tests in 13.58 seconds**. The fresh retained evaluator record is `evaluation/results/evaluation-20261107T070922Z.json` (nine runs; three per fixture; summed case elapsed time 7.493 seconds). Database-pool runs all completed in 9 dispatches; DNS runs all completed in 5. Missing-evidence runs all ended unresolved in 6 dispatches, at board revision 22, with one unavailable request and no accepted cause or solution. Their strict missing-evidence gates passed. The final evaluation recorded 434 successful audit rows, zero audit failures, and 60 completed specialist finishes.

Independent verification caught two intermediate integration gaps while these fixes were being finalized; those provisional evaluator outputs are retained locally but are not the final acceptance record. See `docs/verification-report.md` for the full final checks and interpretation.

The earlier nine-run evaluator record, `evaluation-20261106T181622Z.json`, is historical evidence from before these fixes. Its raw values are preserved. Offline fixture outcomes do not measure live LLM behavior. The configurable provider adapter remains unselected; offline provider-token usage is unmeasured.

## Refreshed local application and submission

After the final code freeze, the default stack was stopped and restarted using only `scripts/stop.py` and `scripts/start.py`. The launcher reported offline mode. All seven new child processes remained alive (Blackboard, Evidence, Diagnosis, Review, Response, controller, and dashboard); all five Agent Cards, controller health, and dashboard health returned HTTP 200. The offline dashboard remains available at `http://127.0.0.1:8501`. Isolated verifier services were not touched.

The refreshed report retains seven pages and the deck eight slides. The historical screenshot labels distinguish them from the fresh final evaluator results. The source archive uses an explicit path allowlist and retains the final result plus historical and provisional failed evaluator records referenced by the verification documents. It excludes `.env`, `.venv`, `.git`, logs, `.data`, caches, and dependency directories.
