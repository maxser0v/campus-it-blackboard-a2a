# Campus IT Incident Investigator

A local multi-agent prototype that investigates synthetic campus IT incidents. Evidence, Diagnosis, Review, and Response specialists communicate over A2A and share versioned findings in a persistent Blackboard. A LangGraph controller routes work from current evidence requests, entry revisions, and review results. LangChain runs specialist prompt/model chains and the allowlisted evidence and runbook tools.

The dashboard displays activity and evidence as it arrives. Its **Agent theatre** tab visualizes the four robot specialists, task activity, and Blackboard exchanges, with live and recorded replay controls for demonstrations. The system recommends diagnostic or recovery steps; it does not connect to campus infrastructure or execute remediation.

## What is included

- A Blackboard A2A service backed by SQLite.
- Four independent A2A specialist services and one separate controller REST API.
- LangGraph conditional routing with evidence, diagnosis, review, response, and finish nodes.
- LangChain chat-model chains and structured tools, supporting the existing Codex CLI, OpenAI-compatible, and offline adapters.
- A Streamlit dashboard that starts a run once, polls status, and reads the Blackboard through its read-only A2A identity.
- Synthetic cases for database pool exhaustion, DNS resolution, and insufficient evidence.
- Fixture evaluation and controller workflow tests.
- A configurable OpenAI-compatible model adapter. The default offline mode is fixture-driven and makes no provider call.

See [the architecture and scheduling rules](docs/architecture.md), [the live demo script](docs/demo-script.md), and [the evaluation method](docs/evaluation.md).

See [the LangGraph and LangChain implementation](docs/langgraph-langchain.md) for the workflow, provider integration, and migration notes.

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

## Choose Groq or Codex CLI

Offline mode remains the default. In live mode, choose the backend with
`CAMPUS_MODEL_PROVIDER`. Omitting it preserves the existing compatible HTTP
adapter, including Groq. `openai_compatible` is also accepted for other compatible
HTTP endpoints.

For Groq, keep your existing provider URL, model, and key:

```dotenv
CAMPUS_MODEL_MODE=live
CAMPUS_MODEL_PROVIDER=groq
CAMPUS_MODEL_BASE_URL=https://api.groq.com/openai/v1
CAMPUS_MODEL_NAME=openai/gpt-oss-120b
CAMPUS_MODEL_API_KEY=your-local-groq-key
CAMPUS_MODEL_TOKEN_LIMIT_PARAMETER=max_completion_tokens
```

For Codex CLI, install a current CLI and sign in with `codex login` first.
Check access with `codex login status`. The adapter was verified with CLI 0.147.0 and 0.169.0. GPT-6 Luna with high reasoning effort was verified using 0.169.0; the older CLI rejected that model.
Set:

```dotenv
CAMPUS_MODEL_MODE=live
CAMPUS_MODEL_PROVIDER=codex_cli
CAMPUS_CODEX_EXECUTABLE=codex
CAMPUS_CODEX_MODEL=
CAMPUS_CODEX_REASONING_EFFORT=
CAMPUS_CODEX_TIMEOUT_SECONDS=180
```

A blank `CAMPUS_CODEX_MODEL` uses the CLI's built-in default; set it to a model
available to your Codex account to override it. This setting is separate from
`CAMPUS_MODEL_NAME`, so an existing Groq model identifier is never sent to Codex.
Set `CAMPUS_CODEX_REASONING_EFFORT=high` for high reasoning effort (or leave it blank for the model default). This selects reasoning depth, not an account usage allowance.
The HTTP URL and Groq key are unused by Codex. Existing file/keychain Codex login
is used; this adapter does not forward API-key environment variables.

Restart with `scripts/stop.py` followed by `scripts/start.py` after changing the
provider. The dashboard shows the active provider and model. Evaluation still
requires `--allow-live` for either provider, and records which provider was used.

Each Codex request runs non-interactively from a temporary folder, in read-only
mode, without loading your user configuration or project files. Shell execution,
plugins, browser/computer use, hooks, and delegation are disabled. A strict JSON
envelope carries the specialist result, which is then checked by the existing
role validators. Failed CLI calls and timeouts are surfaced, with no fabricated
answer or silent fallback to Groq. Reported token usage is recorded; missing
usage remains unmeasured. Codex has its own account usage limits and typically
more prompt overhead than a direct HTTP model call. Adjust `CAMPUS_MAX_RUN_TOKENS`
and `CAMPUS_CASE_TIMEOUT_SECONDS` if needed for your selected model; the defaults
remain unchanged. The controller allows enough time for two CLI calls (initial
response plus one repair), while the investigation deadline remains a separate
bound checked between tasks.
