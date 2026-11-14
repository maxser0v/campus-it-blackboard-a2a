# Independent code review

**Date:** 6 November 2026  
**Scope:** Review against [BLACKBOARD_PROJECT_PLAN.md](../BLACKBOARD_PROJECT_PLAN.md), including A2A boundaries, Blackboard access, answer-key isolation, revision and dispatch handling, token reporting, dashboard reruns, process lifecycle, and configuration.

## Review findings resolved

| Finding | Impact | Resolution and evidence |
| --- | --- | --- |
| The same unavailable metric query could be reopened under new evidence-request IDs. | The `missing_evidence` case repeated a database-pool query that had already returned no records and reached the 12-dispatch cap. | Specialist requests now compare the tool and query with linked unavailable observations. The reviewer suppresses duplicate follow-ups, unchanged hypotheses do not create revisions, and the controller reaches `unresolved`. Three fresh runs each ended after 10 dispatches with one unique unavailable request and no accepted cause. See [runner.py](../src/campus_blackboard/agents/runner.py) and [evaluation results](../evaluation/results/evaluation-20261106T181622Z.json). |
| Evidence requests remained visually `open` after a tool had fulfilled them or returned no records; hypotheses showed only their current revision. | The dashboard could suggest that work was still pending and hide the revisions that explain a diagnosis change. | The dashboard derives request state from linked observations, shows `fulfilled` or `unavailable`, and includes per-hypothesis revision history plus revision details in Activity. See [dashboard.py](../src/campus_blackboard/dashboard.py). |
| Concurrent specialist tasks shared one mutable model-usage list and adapter metadata. | Separate cases could receive mixed token counts, making per-run usage and between-task token-limit checks unreliable. | Normal hosted requests now construct a fresh runner and adapter per task. An explicitly injected shared test adapter is serialized by a handler lock. A concurrent-task regression test checks that usage remains isolated. See [runner.py](../src/campus_blackboard/agents/runner.py) and [test_agents_roles.py](../tests/unit/test_agents_roles.py). |
| The evaluator compared dispatches to a fixed limit of 12. | Changing the documented `CAMPUS_MAX_DISPATCHES` setting could produce a false pass or failure. | The check now uses the limit saved with the problem and falls back to current settings. Tests cover a non-12 persisted limit and a non-12 fallback. See [evaluate.py](../scripts/evaluate.py) and [test_evaluate.py](../tests/unit/test_evaluate.py). |
| A startup attempt could mistake an old service’s readiness response for a newly launched child, and process-inspection failure could be treated as a dead process. | The launcher could overwrite its process journal or report success while stale services still owned the ports. | Process inspection now distinguishes a missing PID from an inspection error and preserves the journal when inspection is unavailable. Startup probes every port before launch and verifies all child PIDs after readiness URLs respond. Stop inspects the full journal before signaling any process and preserves it if inspection fails. Seven launcher regression tests pass. See [start.py](../scripts/start.py), [stop.py](../scripts/stop.py), and [test_service_scripts.py](../tests/unit/test_service_scripts.py). |

## Architecture and safety checks

- Blackboard and specialist calls use the pinned A2A SDK, Agent Card discovery, and JSON-RPC. The controller’s REST API is limited to dashboard run and status operations; it is not presented as A2A.
- SQLite connections are confined to the Blackboard repository. Specialists and the dashboard use the Blackboard service.
- `evaluation/answer_keys/` is referenced by the evaluator only. Agent tools read the synthetic fixture and runbook roots; no answer-key fields were found in Blackboard contributions.
- The offline adapter contains deterministic fixture rules, but its mode is labelled in the dashboard and startup output and makes no provider call. The live adapter uses the configured provider; it has no hard-coded live diagnosis fallback.
- Reviews target exact entry revisions. Semantic dispatch keys prevent repeated work for unchanged state, and deterministic Blackboard request IDs make duplicate task writes idempotent.
- Offline token usage is reported as unmeasured. Live usage is taken only from provider-reported usage fields. In-flight calls can finish after the run-token ceiling is reached because the limit is checked between tasks.
- Streamlit refresh reads state; starting work requires the explicit button and a new client request ID.
- `.env` is ignored by Git. The project startup and stop scripts use recorded PIDs and command markers; the scripts now fail safely when process inspection is unavailable.

## Remaining limitation

The plan named one transport retry with the same application request ID as a configuration target. The A2A client currently surfaces a transient dispatch failure to the controller, which ends that run as `blocked`; `CAMPUS_MAX_RETRIES` is used by the evaluator’s idempotent status and Blackboard reads, not by specialist dispatch. The local fixture acceptance did not exercise intermittent A2A network failures.

## Verification boundary

The final test suite passed **61 tests**. The evaluator ran three repetitions for each fixture case in offline mode. No external model provider was contacted, and these runs are not a statistical success claim. See [verification-report.md](verification-report.md) and [implementation-status.md](implementation-status.md) for measured outcomes and limits.
