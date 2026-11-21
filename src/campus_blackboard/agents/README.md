# Reading the agents

Start with the role you want to understand. Each role has an ordinary async
function that reads the supplied context, does one task, and publishes results.

| File | Main function | Job |
| --- | --- | --- |
| `evidence.py` | `collect_evidence` | Run a query and publish observations with their sources. |
| `diagnosis.py` | `diagnose` | Compare explanations, publish or revise hypotheses, and request missing evidence. |
| `review.py` | `review` | Check one exact revision and publish an accept, reject, or evidence-request verdict. |
| `response.py` | `propose_response` | Turn an accepted diagnosis and local runbooks into proposed recovery steps. |

Read `runner.py` next. `SpecialistRunner.run` checks the task, reads the Blackboard,
and calls the matching role function. Roles use two shared operations:

- `agent.call_blackboard(...)` reads or publishes shared knowledge over A2A.
- `agent.ask_model(...)` invokes the LangChain chain, validates the response, and
  allows one correction if the response is invalid.

`common.py` contains small entry and query helpers. `validation.py` contains the
checks that keep findings tied to real evidence, owned entries and exact
revisions. These checks remain separate from the main workflows so the role files
are easier to read.

`server.py` hosts the agents as independent A2A services. Each role retains its
`build_handler` factory. The LangGraph controller still decides which agent gets
the next task; none of these files changes the controller's routing rules.

Repeated Evidence deliveries reuse their saved result. Normal service tasks each
get a fresh model adapter; an injected shared adapter is protected by a lock.
Those details prevent duplicate observations and mixed usage records.
