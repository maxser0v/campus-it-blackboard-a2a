# Campus IT Incident Investigator

A local multi-agent prototype that investigates synthetic campus IT incidents. Evidence, Diagnosis, Review, and Response specialists communicate over A2A and share versioned findings in a persistent Blackboard. A deterministic controller schedules work from current evidence requests, entry revisions, and review results.

The dashboard displays activity and evidence as it arrives. The system recommends diagnostic or recovery steps; it does not connect to campus infrastructure or execute remediation.

## What is included

- A Blackboard A2A service backed by SQLite.
- Four independent A2A specialist services and one separate controller REST API.
- A Streamlit dashboard that starts a run once, polls status, and reads the Blackboard through its read-only A2A identity.
- Synthetic cases for database pool exhaustion, DNS resolution, and insufficient evidence.
- Fixture evaluation and controller workflow tests.
- A configurable OpenAI-compatible model adapter. The default offline mode is fixture-driven and makes no provider call.

See [the architecture and scheduling rules](docs/architecture.md), [the live demo script](docs/demo-script.md), and [the evaluation method](docs/evaluation.md).

## Setup

Requirements: Python 3.11 and [uv](https://docs.astral.sh/uv/). The project pins A2A SDK 1.3.0 and all resolved dependencies in `uv.lock`.

```bash
git clone https://github.com/maxser0v/campus-it-blackboard-a2a.git
cd campus-it-blackboard-a2a
uv sync --locked --extra dev
uv run --locked python scripts/setup_env.py
uv run --locked python scripts/start.py
```

`setup_env.py` creates `.env` with unique local service tokens only if the file is absent; it leaves an existing `.env` unchanged. The dashboard credential is read-only. Keep `.env` local and do not commit it.

The default `.env.example` selects `CAMPUS_MODEL_MODE=offline`. This mode is clearly labelled in the dashboard and uses the local fixture adapter without contacting a model provider. To use a live compatible provider later, set `CAMPUS_MODEL_MODE=live`, `CAMPUS_MODEL_BASE_URL`, `CAMPUS_MODEL_NAME`, and `CAMPUS_MODEL_API_KEY` for the chosen provider. The project does not select a provider or model identifier for you.

Optional local settings include:

| Setting | Default | Meaning |
| --- | --- | --- |
| `CAMPUS_DATABASE_PATH` | `.data/blackboard.sqlite3` | Blackboard database file |
| `CAMPUS_MAX_DISPATCHES` | `12` | Maximum specialist tasks per case |
| `CAMPUS_CASE_TIMEOUT_SECONDS` | `300` | Investigation deadline |
| `CAMPUS_MAX_RUN_TOKENS` | `12000` | Provider-reported token ceiling checked between tasks |
| `CAMPUS_REQUEST_TIMEOUT` | `120` | A2A client timeout in seconds |
| `CAMPUS_MODEL_TOKEN_LIMIT_PARAMETER` | `max_tokens` | Optional output-token parameter name for compatible providers |

Offline token usage is unmeasured. The controller does not invent a token count. In live mode, missing usage remains unmeasured or partial.

## Start the application

The commands above start the Blackboard, four specialists, controller API, and dashboard as tracked local processes. If they are already configured, start them with:

```bash
uv run --locked python scripts/start.py
```

Open the dashboard at <http://127.0.0.1:8501>. Select a case and press **Start a fresh investigation**. Dashboard refresh reads state only; it does not dispatch more work. To start a separate run, press the button again to create a new problem ID.

The controller API is at <http://127.0.0.1:8101>. The start and status endpoints are `POST /runs` and `GET /runs/{problem_id}`. Logs and process IDs are under `logs/`.

Stop only the processes started by this project:

```bash
uv run --locked python scripts/stop.py
```

The stop script checks each recorded PID against the command it launched before sending a signal.

## Evaluate fixture runs

With the services running, evaluate each fixture case once:

```bash
uv run --locked python scripts/evaluate.py
```

Repeat the main database-pool case three times with new problem IDs:

```bash
uv run --locked python scripts/evaluate.py --case db_pool --repeat 3
```

Select a different case with `--case dns_fault` or `--case missing_evidence`. The script writes measured results to `evaluation/results/`. It records terminal status, elapsed time, dispatch count, entry counts, review and citation checks, and a rough ground-truth label match. The label match is not a correctness proof; inspect the cited fixture records and reasoning manually. All runs, including failures, should be retained and reviewed.

When `CAMPUS_MODEL_MODE=live`, evaluation refuses provider calls unless `--allow-live` is passed explicitly. Running live calls may incur provider cost depending on the selected account.

## Tests and checks

Run the tests with:

```bash
uv run --locked python -m pytest
```

The focused scheduler and workflow tests run without a model provider or network service. The broader integration suite exercises the real local A2A transport. See `docs/evaluation.md` for how to record actual case results separately from test results.

## Services and ports

| Service | Default endpoint |
| --- | --- |
| Blackboard A2A | `http://127.0.0.1:8100` |
| Controller REST API | `http://127.0.0.1:8101` |
| Evidence Agent A2A | `http://127.0.0.1:8102` |
| Diagnosis Agent A2A | `http://127.0.0.1:8103` |
| Review Agent A2A | `http://127.0.0.1:8104` |
| Response Agent A2A | `http://127.0.0.1:8105` |
| Dashboard | `http://127.0.0.1:8501` |

Ports, host, service URLs, database location, model mode, and limits are configurable in `.env`. A2A services publish `/.well-known/agent-card.json` and accept JSON-RPC at `/a2a/jsonrpc/`.
