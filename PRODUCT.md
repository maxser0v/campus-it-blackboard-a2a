# Campus IT Incident Investigator

A local university demonstration prototype for investigating synthetic campus IT incidents. Presenters and viewers follow how evidence is collected, a diagnosis is challenged and revised, and a bounded response is proposed.

## Product behavior

- Four specialist agents—Evidence, Diagnosis, Review, and Response—communicate through A2A and share versioned findings in a persistent SQLite-backed Blackboard.
- A separate deterministic controller schedules specialist tasks using evidence requests, entry revisions, and review outcomes.
- The Streamlit dashboard starts a fresh investigation when the user presses its start action. Its recurring status and Blackboard reads use a read-only dashboard identity; refresh does not dispatch additional work.
- The dashboard exposes observations, hypotheses, activity, controller decisions, response proposals, and an embedded Agent theatre with robot selection and live/recorded replay controls.
- Reviews apply to exact finding or proposal revisions. Source references, request outcomes, and raw details remain inspectable.
- Included synthetic cases cover database pool exhaustion, DNS resolution, and insufficient evidence.

## Runtime truth

Offline mode is the default and uses local fixtures without model-provider calls. Live mode uses a configured compatible HTTP provider or Codex CLI backend. The dashboard labels the actual runtime mode and configured provider/model. Missing usage remains unmeasured or partial rather than becoming an invented token count.

## Scope and audience

The primary use is demonstrating an investigation on a laptop or classroom projector. The prototype recommends diagnostic or recovery steps; it does not connect to campus infrastructure or execute remediation. A fresh investigation and a recorded replay must retain their distinct actual mode and provenance.

## Visual continuity

The user explicitly chose to retain the existing Agent theatre identity while reworking the rest of the dashboard and tabs. The implemented extension uses navy grounds, pale blue controls, consistent specialist colors, readable findings, and native Streamlit interaction. DESIGN.md records the current visual system.

This document records verified behavior from README.md, src/campus_blackboard/dashboard.py, src/campus_blackboard/dashboard_ui.py, and the accepted dashboard surface direction; it introduces no commercial or deployment claims.
