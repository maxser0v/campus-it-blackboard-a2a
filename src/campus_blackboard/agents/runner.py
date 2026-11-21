"""Shared agent setup, model calls and task routing.

Read evidence.py, diagnosis.py, review.py and response.py for the agent workflows.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from ..langchain_adapter import specialist_chain
from ..model_adapter import ModelAdapter, model_from_settings
from ..tools import ToolRegistry, read_case_summary
from ..tools.fixtures import read_query_catalog
from .common import PURPOSES, ROLES, SpecialistRunError, blackboard_entries, is_error
from .diagnosis import diagnose
from .evidence import collect_evidence, completed_evidence
from .response import propose_response
from .review import review

_PROMPT_ROOT = Path(__file__).resolve().parents[3] / "prompts"


def _prompt(role: str) -> str:
    path = _PROMPT_ROOT / f"{role}.md"
    return path.read_text(encoding="utf-8")


class SpecialistRunner:
    """Shared task lifecycle; the role modules contain each agent's workflow."""

    def __init__(
        self,
        role: str,
        *,
        blackboard: Any,
        model: ModelAdapter,
        tools: ToolRegistry | None = None,
    ) -> None:
        if role not in ROLES:
            raise ValueError(f"unknown specialist role: {role}")
        self.role = role
        self.blackboard = blackboard
        self.model = model
        self.chain = specialist_chain(model)
        self.tools = tools or ToolRegistry()
        self.actor = str(getattr(blackboard, "actor", role))
        self.last_run_usage: list[dict[str, Any]] = []

    async def call_blackboard(self, action: str, **params: Any) -> dict[str, Any]:
        """Send one A2A Blackboard action and reject unsuccessful responses."""
        result = await self.blackboard.call(action, **params)
        if not isinstance(result, dict):
            raise SpecialistRunError(
                f"Blackboard {action} returned a non-object result"
            )
        if is_error(result):
            raise SpecialistRunError(
                f"Blackboard {action} failed: {result.get('error') or result}"
            )
        return result

    async def ask_model(
        self, purpose: str, context: dict[str, Any], validator: Callable[[Any], Any]
    ) -> Any:
        """Ask for a decision, validate it, and allow one corrected response."""
        instructions = _prompt(self.role)
        context = dict(context)
        schema = context.pop("_output_schema")
        context["available_tools"] = {
            "query_metrics": {
                "metric_names": "optional list of exact metric names",
                "contains": "optional string or null",
            },
            "query_logs": {
                "contains": "optional string matching ALL words",
                "service": "optional exact service name",
                "level": "optional single lowercase debug/info/warning/error/critical or null",
                "limit": "optional integer 1..100",
            },
            "get_change_history": {
                "service": "optional exact service name",
                "limit": "optional integer 1..100",
            },
        }
        context["tool_query_rules"] = (
            "Only listed argument keys are allowed. query_metrics does NOT accept service, limit, time ranges, or filters. There are no DNS lookup or infrastructure tools. An evidence request must use requested_tool and query, not tool or arguments. Request unavailable evidence using a valid query; do not invent a tool."
        )

        for attempt in range(2):
            raw = await self._invoke_model(
                purpose, instructions, context, schema, attempt + 1
            )
            try:
                return validator(json.loads(raw))
            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
                if attempt == 1:
                    raise SpecialistRunError(
                        f"model output invalid after one repair attempt: {error}"
                    ) from error
                context = {
                    **context,
                    "repair_feedback": {
                        "previous_output": raw[:6000],
                        "validation_error": str(error)[:1500],
                        "instruction": "Return a corrected JSON object that satisfies the original schema and all source-reference rules.",
                    },
                }
        raise AssertionError("unreachable")

    async def _invoke_model(
        self,
        purpose: str,
        instructions: str,
        context: dict[str, Any],
        schema: dict[str, Any],
        attempt: int,
    ) -> str:
        """Run the LangChain chain and retain provider-reported usage, even on failure."""
        task = {
            "role": self.role,
            "purpose": purpose,
            "context": context,
            "output_schema": schema,
        }
        try:
            raw = await self.chain.ainvoke(
                {"instructions": instructions, "task": json.dumps(task)},
                config={
                    "run_name": f"{self.role}:{purpose}",
                    "tags": ["specialist", self.role],
                    "metadata": {
                        "role": self.role,
                        "purpose": purpose,
                        "attempt": attempt,
                    },
                },
            )
        finally:
            metadata = getattr(self.model, "last_call_metadata", None)
            if isinstance(metadata, Mapping):
                self.last_run_usage.append(dict(metadata))
        if not isinstance(raw, str):
            raise SpecialistRunError("model adapter returned non-text output")
        return raw

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate the task, read its case, then run the matching agent workflow."""
        if not isinstance(payload, dict):
            raise SpecialistRunError("task payload must be an object")
        problem_id = payload.get("problem_id")
        purpose = payload.get("purpose")
        dispatch_id = payload.get("dispatch_id")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (problem_id, purpose, dispatch_id)
        ):
            raise SpecialistRunError(
                "task requires problem_id, purpose, and dispatch_id"
            )
        if purpose not in PURPOSES[self.role]:
            raise SpecialistRunError(
                f"purpose {purpose!r} is not permitted for role {self.role!r}"
            )
        self.last_run_usage = []
        snapshot = await self.call_blackboard("read_blackboard", problem_id=problem_id)
        problem = snapshot.get("problem")
        if not isinstance(problem, Mapping) or problem.get("problem_id") != problem_id:
            raise SpecialistRunError(
                "Blackboard snapshot does not contain the requested problem"
            )
        case_id = problem.get("case_id")
        if not isinstance(case_id, str):
            raise SpecialistRunError("Blackboard problem has no fixture case_id")
        if purpose == "collect_evidence":
            replay = completed_evidence(
                self, snapshot, problem_id, case_id, dispatch_id
            )
            if replay is not None:
                return replay
        summary = read_case_summary(case_id)
        summary["query_catalog"] = read_query_catalog(case_id)
        entries = blackboard_entries(snapshot)

        if purpose == "collect_evidence":
            return await collect_evidence(
                self, problem_id, case_id, dispatch_id, summary, entries
            )
        elif purpose == "diagnose":
            result, summary_text = await diagnose(
                self, problem_id, dispatch_id, summary, entries
            )
        elif purpose in {"review_hypotheses", "review_solution"}:
            result, summary_text = await review(
                self, problem_id, purpose, dispatch_id, summary, entries
            )
        else:
            result, summary_text = await propose_response(
                self, problem_id, dispatch_id, summary, entries
            )
        return {
            "ok": True,
            "role": self.role,
            "problem_id": problem_id,
            "purpose": purpose,
            "dispatch_id": dispatch_id,
            "entry_ids": result,
            "summary": summary_text,
            "model_mode": getattr(self.model, "mode", "unknown"),
            "model_usage": list(self.last_run_usage),
        }


def build_specialist_handler(
    role: str,
    settings: Any | None = None,
    *,
    blackboard: Any | None = None,
    model: ModelAdapter | None = None,
    tools: ToolRegistry | None = None,
) -> Callable[[dict[str, Any]], Any]:
    """Create the async A2A handler expected by `create_a2a_app`.

    The optional injections make tests deterministic. Normal service startup
    creates a per-task model adapter and runner from Settings. An explicitly
    injected adapter is shared only under a handler lock.
    """
    if role not in ROLES:
        raise ValueError(f"unknown specialist role: {role}")
    if settings is None:
        from ..config import get_settings

        settings = get_settings()
    if blackboard is None:
        from ..blackboard.client import BlackboardClient

        urls = getattr(settings, "agent_urls", {})
        url = (
            getattr(settings, "blackboard_url", None)
            or (urls.get("blackboard") if isinstance(urls, Mapping) else None)
            or "http://127.0.0.1:8100"
        )
        credentials = getattr(settings, "agent_credentials", {})
        actor = role
        token = credentials.get(actor) if isinstance(credentials, Mapping) else None
        blackboard = BlackboardClient(
            base_url=url,
            actor=actor,
            api_key=token,
            timeout=float(getattr(settings, "request_timeout", 30.0)),
        )
    if model is None:

        async def handle(payload: dict[str, Any]) -> dict[str, Any]:
            runner = SpecialistRunner(
                role,
                blackboard=blackboard,
                model=model_from_settings(settings),
                tools=tools,
            )
            return await runner.run(payload)

        return handle

    runner = SpecialistRunner(role, blackboard=blackboard, model=model, tools=tools)
    shared_adapter_lock = asyncio.Lock()

    async def handle(payload: dict[str, Any]) -> dict[str, Any]:
        async with shared_adapter_lock:
            return await runner.run(payload)

    return handle
