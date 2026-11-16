"""Reusable A2A JSON-RPC service factory with credential-derived identity."""

from __future__ import annotations

import asyncio
import hmac
import inspect
import json
import logging
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Mapping

from fastapi import FastAPI
from starlette.middleware import Middleware
from starlette.responses import JSONResponse

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Role, Task, TaskState, TaskStatus

from campus_blackboard.config import Settings, get_settings
from campus_blackboard.transport.cards import create_agent_card
from campus_blackboard.transport.parts import extract_structured_payload, structured_message


_authenticated_actor: ContextVar[str | None] = ContextVar("campus_a2a_actor", default=None)
Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


class CredentialActorMiddleware:
    """Authenticate A2A calls and bind identity from a configured token."""

    def __init__(self, app: Any, *, role: str, credentials: Mapping[str, str]):
        self.app = app
        self.role = role
        self.credentials = dict(credentials)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") == "/.well-known/agent-card.json":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        supplied = supplied or headers.get(b"x-api-key", b"").decode("latin-1")
        actor = next(
            (candidate for candidate, token in self.credentials.items() if hmac.compare_digest(supplied, token)),
            None,
        ) if supplied else None

        allowed = actor is not None and (
            self.role == "blackboard" or actor == self.role or actor == "controller"
        )
        if not allowed:
            response = JSONResponse(
                {"error": {"code": "unauthorized", "message": "A valid service credential is required."}},
                status_code=401 if actor is None else 403,
            )
            await response(scope, receive, send)
            return

        token = _authenticated_actor.set(actor)
        try:
            await self.app(scope, receive, send)
        finally:
            _authenticated_actor.reset(token)


class StructuredAgentExecutor(AgentExecutor):
    def __init__(self, role: str, handler: Handler | None, repository: Any | None):
        self.role = role
        self.handler = handler
        self.repository = repository

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            payload = extract_structured_payload(context.message)
            if self.repository is not None:
                actor = _authenticated_actor.get()
                if actor is None:
                    result = _error("unauthorized", "No authenticated actor is bound to this request.")
                else:
                    result = await asyncio.to_thread(self.repository.handle, actor, payload)
            else:
                await self._execute_task(context, event_queue, payload)
                return
            if not isinstance(result, dict):
                raise TypeError("A2A service handlers must return a JSON object")
            result = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False, default=_json_default))
        except Exception as exc:
            logging.getLogger(__name__).exception("%s service request failed", self.role)
            result = _error("request_failed", "The service could not process this request.", error_type=type(exc).__name__)

        await event_queue.enqueue_event(structured_message(result, role=Role.ROLE_AGENT))

    async def _execute_task(
        self, context: RequestContext, event_queue: EventQueue, payload: dict[str, Any]
    ) -> None:
        task_id = context.task_id
        context_id = context.context_id
        if not task_id or not context_id or context.message is None:
            raise ValueError("A2A task request is missing task, context, or message identifiers")
        await event_queue.enqueue_event(
            Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[context.message],
            )
        )
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.start_work()
        try:
            if self.handler is None:
                raise RuntimeError("This A2A service has no request handler.")
            value = self.handler(payload)
            if inspect.isawaitable(value):
                value = await value
            if not isinstance(value, dict):
                raise TypeError("A2A service handlers must return a JSON object")
            result = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False, default=_json_default))
            result["_a2a"] = {
                "task_id": task_id,
                "context_id": context_id,
                "state": "TASK_STATE_COMPLETED",
            }
            artifact = structured_message(result, role=Role.ROLE_AGENT)
            await updater.add_artifact(artifact.parts, name="result", last_chunk=True)
            await updater.complete()
        except Exception as exc:
            logging.getLogger(__name__).exception("%s specialist task %s failed", self.role, task_id)
            failure = _error(
                "request_failed",
                "The service could not process this request.",
                error_type=type(exc).__name__,
            )
            failure["_a2a"] = {
                "task_id": task_id,
                "context_id": context_id,
                "state": "TASK_STATE_FAILED",
            }
            artifact = structured_message(failure, role=Role.ROLE_AGENT)
            await updater.add_artifact(artifact.parts, name="result", last_chunk=True)
            await updater.failed()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Short request handlers have no running task to cancel."""

        return None


def create_a2a_app(
    role: str,
    handler: Handler | None = None,
    settings: Settings | None = None,
    *,
    repository: Any | None = None,
    base_url: str | None = None,
) -> FastAPI:
    """Create a discoverable A2A v1 JSON-RPC FastAPI service.

    Blackboard requests use ``repository.handle(authenticated_actor, payload)``;
    other roles use ``handler(payload)`` and return a Task with an artifact.
    Actor identity comes from a mapped bearer token (or ``X-API-Key`` header),
    never from message data.
    """

    if (handler is None) == (repository is None):
        raise ValueError("provide exactly one of handler or repository")
    settings = settings or get_settings()
    base_url = (base_url or _base_url_for(role, settings)).rstrip("/")
    card = create_agent_card(role, base_url)
    executor = StructuredAgentExecutor(role, handler, repository)
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    routes = create_agent_card_routes(card)
    routes.extend(create_jsonrpc_routes(request_handler, rpc_url="/a2a/jsonrpc/"))
    middleware = [
        Middleware(
            CredentialActorMiddleware,
            role=role,
            credentials=settings.agent_credentials,
        )
    ]
    return FastAPI(title=card.name, routes=routes, middleware=middleware)


def authenticated_actor() -> str | None:
    """Return the authenticated actor for internal code in this request."""

    return _authenticated_actor.get()


def _base_url_for(role: str, settings: Settings) -> str:
    if role in settings.agent_urls:
        return settings.agent_urls[role]
    ports = {
        "blackboard": settings.blackboard_port,
        "controller": settings.controller_port,
        "evidence": 8102,
        "diagnosis": 8103,
        "review": 8104,
        "response": 8105,
    }
    try:
        port = ports[role]
    except KeyError as exc:
        raise ValueError(f"unsupported A2A role: {role}") from exc
    return f"http://{settings.bind_host}:{port}"


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")

