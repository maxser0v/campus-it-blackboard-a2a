# Live demonstration script (target: 11 minutes)

Use the live `db_pool` case in the Streamlit dashboard. Keep the case marked **Offline model mode** visible when using fixture-driven agent responses. Do not describe a recommendation as a repair that happened.

| Time | Presenter action and explanation |
| --- | --- |
| 0:00–1:00 | State the symptom: students see intermittent timeouts and slow learning-portal pages. Explain that the system will investigate synthetic logs and metrics and recommend a bounded response. |
| 1:00–2:30 | Point to the dashboard and service diagram. Explain the Blackboard as shared persistent memory and A2A as the service-to-service message path. Identify Evidence, Diagnosis, Review, and Response roles. |
| 2:30–3:30 | Start a fresh `db_pool` investigation. Show the first observations and the controller decision that fired. Explain that traffic overload and pool exhaustion remain plausible at this point. |
| 3:30–4:30 | Open the hypothesis view. Show the reviewer’s specific request for a pool measurement. Follow the controller decision and linked evidence observation for the targeted query. |
| 4:30–5:30 | Show the revised hypothesis and its supporting observation IDs. Point out that earlier revisions remain in the activity history. State which measurement changed the diagnosis. |
| 5:30–6:30 | Show the Response proposal, cited observations, runbook ID, and exact solution review. Emphasize the recommendation is not executed. |
| 6:30–8:00 | Expand a controller decision. Show its dispatch ID, A2A task ID if returned, purpose, trigger rule, and result entry IDs. Describe the persistent revisions behind the dashboard. |
| 8:00–9:30 | Run or show the `missing_evidence` case. Highlight an unavailable requested query and the final unresolved or blocked result. Do not invent a cause. |
| 9:30–10:30 | Explain what the evaluation script measures: statuses, dispatches, entry counts, evidence references, review gates, and case-specific checks. Distinguish automated checks from manual claim review. |
| 10:30–11:00 | Summarize the value of conditional coordination, the synthetic-data limitation, and each member’s actual contribution. Leave time for questions. |

## If a service is slow or unavailable

Use the dashboard’s current board state and point out whether the controller reports `blocked` for an operational failure or `unresolved` because the evidence was insufficient. If a recorded screenshot or run is used, label it as recorded. Do not imply that recorded state is a live run.

## Suggested walkthrough questions

- Which observation caused the Diagnosis Agent to reconsider its explanations?
- Which exact hypothesis revision did the Review Agent approve?
- Which evidence and runbook IDs support the response?
- What would cause the controller to stop without a diagnosis?
