# Live validation — 17 November 2026

Provider: Codex CLI 0.169.0; model: gpt-6-luna; reasoning effort: high.

Full automated regression suite: **139 passed**.

| Scenario | Expected result | Measured result | Agent tasks | Time |
| --- | --- | --- | --- | --- |
| Database pool | Completed, reviewed diagnosis and response | Completed | 5 | 57.742 s |
| DNS fault | Completed, reviewed diagnosis and response | Completed | 5 | 62.274 s |
| Missing evidence | Unresolved for an evidence reason, no accepted cause or response | Unresolved: repeated_queries_returned_no_evidence | 6 | 54.751 s |

The successful final outcomes are from two runs. Database and DNS results are in
[evaluation-20261117T082359Z.json](../evaluation/results/evaluation-20261117T082359Z.json).
That run's missing-evidence measurement was interrupted by a disk-full service
failure. The successful isolated missing-evidence rerun is in
[evaluation-20261117T082848Z.json](../evaluation/results/evaluation-20261117T082848Z.json).
Earlier failed measurements remain available; they have not been overwritten.

Database and DNS passed the current diagnosis review, response review, evidence
reference and runbook-reference gates. Their rough ground-truth label string
checks returned false; diagnoses were inspected separately against the fixture
measurements. An exact phrase check is not a semantic correctness test.
Missing-evidence passed all three scenario-specific insufficient-evidence checks:
an unavailable query was recorded, the stop was evidence-related, and no causal
hypothesis or response was accepted. Positive-response checks are intentionally
false for this unresolved scenario.

The missing-evidence convergence fix stops after two successive unavailable
requested observations and a fresh diagnosis that has considered both without
supporting a cause. It preserves the supported-hypothesis review path. The
evaluator now normalizes integer-valued A2A numeric limits before comparing
requests and observations.

The Mac exhausted free disk space during verification. Only the two cached
Codex 0.169.0 installer downloads created during the update were removed; the
installed CLI and project data remain. Available space after the rerun was
approximately 278 MB, so more free space is still needed for reliable continued
use. No infrastructure recovery actions were executed by the application.
