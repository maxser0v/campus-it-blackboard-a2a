"""A2A JSON-RPC client with Agent Card discovery and structured messaging."""

from __future__ import annotations

from typing import Any

import httpx
from google.protobuf.json_format import MessageToDict
from a2a.client import ClientConfig, create_client
from a2a.types import SendMessageConfiguration, SendMessageRequest

from campus_blackboard.config import get_settings
from campus_blackboard.transport.parts import extract_structured_response, structured_message


class BlackboardActionError(RuntimeError):
    """A structured error returned by the Blackboard action layer."""

    def __init__(self, error: dict[str, Any]):
        self.code = str(error.get("code", "action_failed"))
        self.details = error.get("details", {}) if isinstance(error.get("details", {}), dict) else {}
        super().__init__(str(error.get("message", "Blackboard action failed")))


async def call_agent(
    base_url: str,
    payload: dict[str, Any],
    actor: str,
    api_key: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Discover an A2A Agent Card, send structured JSON, and parse its reply."""

    if not base_url:
        raise ValueError("base_url is required")
    if not actor:
        raise ValueError("actor is required to select the local service credential")
    settings = get_settings() if api_key is None or timeout is None else None
    token = api_key or (settings.agent_credentials.get(actor) if settings else None)
    if not token:
        raise ValueError(f"no configured A2A credential is available for actor '{actor}'")
    effective_timeout = timeout if timeout is not None else settings.request_timeout

    async with httpx.AsyncClient(
        timeout=effective_timeout,
        headers={"Authorization": f"Bearer {token}"},
    ) as http_client:
        client = await create_client(
            base_url.rstrip("/"),
            client_config=ClientConfig(
                streaming=True,
                supported_protocol_bindings=["JSONRPC"],
                accepted_output_modes=["application/json"],
                httpx_client=http_client,
            ),
        )
        try:
            request = SendMessageRequest(
                message=structured_message(payload),
                configuration=SendMessageConfiguration(
                    accepted_output_modes=["application/json"],
                    return_immediately=False,
                ),
            )
            response_message = None
            task_payload = None
            task_id = None
            context_id = None
            task_state = None
            async for response in client.send_message(request):
                if response.HasField("message"):
                    response_message = response.message
                elif response.HasField("task"):
                    task_id = response.task.id
                    context_id = response.task.context_id
                    task_state = response.task.status.state
                elif response.HasField("artifact_update"):
                    parts = response.artifact_update.artifact.parts
                    for part in parts:
                        if part.HasField("data"):
                            candidate = MessageToDict(part.data)
                            if isinstance(candidate, dict):
                                task_payload = candidate
                elif response.HasField("status_update"):
                    update = response.status_update
                    task_state = update.status.state
                    task_id = update.task_id or task_id
                    context_id = update.context_id or context_id

            if task_id:
                if task_payload is None and response_message is not None:
                    task_payload = extract_structured_response(response_message)
                if task_payload is None:
                    raise RuntimeError("A2A task completed without a structured result artifact")
                result = task_payload
                result["_a2a"] = {
                    "task_id": task_id,
                    "context_id": context_id,
                    "state": _task_state_name(task_state),
                }
            elif response_message is not None:
                result = extract_structured_response(response_message)
            else:
                raise RuntimeError("A2A service returned no message or task result")
            if isinstance(result.get("error"), dict):
                raise BlackboardActionError(result["error"])
            return result
        finally:
            await client.close()


def _task_state_name(state: int | None) -> str | None:
    if state is None:
        return None
    from a2a.types import TaskState

    try:
        return TaskState.Name(state)
    except ValueError:
        return str(state)

