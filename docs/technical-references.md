# Technical references

The application pins `a2a-sdk[fastapi]==1.3.0` and uses the A2A Python SDK v1 JSON-RPC binding. These primary references informed the implementation and compatibility checks:

- [A2A Python v1.3.0 hello world server sample](https://github.com/a2aproject/a2a-python/blob/v1.3.0/samples/hello_world_agent.py) — server executor, request handler, Agent Card, JSON-RPC route, task lifecycle, and structured message pattern.
- [A2A Python v1.3.0 migration guide](https://github.com/a2aproject/a2a-python/blob/v1.3.0/docs/migrations/v1_0/README.md) — v1 `Message`/`Part` structured payload conventions.

Implementation notes checked against those tagged sources:

- A2A `Message` requires a message ID. A structured protobuf data part is built with `ParseDict(payload, Value())`.
- `DefaultRequestHandler` receives an Agent Card and task store. The service publishes the card with `create_agent_card_routes` and JSON-RPC with `create_jsonrpc_routes`.
- Long specialist work uses `TaskUpdater`: start the task, add a structured artifact, then mark it complete or failed.
- The client discovers the Agent Card and selects JSON-RPC through `ClientConfig` before sending a structured message.
- The Blackboard action envelope is project data carried by A2A; it is not implemented as a set of custom RPC methods.

Other project choices are documented in [architecture.md](architecture.md). Dependency versions are recorded in `uv.lock`.
