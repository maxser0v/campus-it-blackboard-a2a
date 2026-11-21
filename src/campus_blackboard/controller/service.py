"""REST controller API used by the dashboard; Blackboard remains remote."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from campus_blackboard.config import Settings, get_settings
from campus_blackboard.blackboard.client import BlackboardClient
from campus_blackboard.transport.client import call_agent

from .runner import ControllerRunner, RunResult


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = PROJECT_ROOT / "fixtures"


class StartRunRequest(BaseModel):
    case_id: str
    client_request_id: str = Field(min_length=1, max_length=120)


@dataclass
class ActiveRun:
    problem_id: str
    case_id: str
    title: str
    started_at: str
    task: asyncio.Task[RunResult]


def _case_catalog() -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    if not FIXTURE_ROOT.exists():
        return cases
    for path in sorted(FIXTURE_ROOT.glob("*/case.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            case_id = str(data["case_id"])
            cases[case_id] = {
                "case_id": case_id,
                "title": str(data["title"]),
                "symptoms": str(data.get("symptoms", "")),
            }
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            continue
    return cases


def create_controller_app(
    *, settings: Settings | None = None, blackboard: Any | None = None
) -> FastAPI:
    settings = settings or get_settings()
    board = blackboard or BlackboardClient(
        f"http://{settings.bind_host}:{settings.blackboard_port}",
        actor="controller",
        api_key=settings.agent_credentials.get("controller"),
        timeout=settings.specialist_timeout,
    )
    app = FastAPI(title="Campus Blackboard Controller", version="0.1.0")
    app.state.settings = settings
    app.state.blackboard = board
    app.state.runs = {}
    app.state.client_requests = {}
    app.state.start_lock = asyncio.Lock()

    async def dispatch_agent(role: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = settings.agent_urls.get(role)
        if not url:
            raise RuntimeError(f"No A2A endpoint is configured for the {role} agent")
        return await call_agent(
            url,
            payload,
            actor="controller",
            api_key=settings.agent_credentials.get("controller"),
            timeout=settings.specialist_timeout,
        )

    runner = ControllerRunner(
        board,
        dispatch_agent,
        max_dispatches=settings.max_dispatches,
        timeout_seconds=settings.case_timeout_seconds,
        max_run_tokens=getattr(settings, "max_run_tokens", 12000),
    )
    app.state.runner = runner

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "controller",
            "orchestrator": "langgraph",
            "agent_framework": "langchain",
            "model_mode": settings.model_mode,
            "model_name": settings.model_name if settings.model_mode == "live" else None,
        }

    @app.get("/cases")
    async def list_cases() -> list[dict[str, str]]:
        return list(_case_catalog().values())

    @app.post("/runs", status_code=202)
    async def start_run(request: StartRunRequest) -> dict[str, Any]:
        catalog = _case_catalog()
        case = catalog.get(request.case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Unknown case_id")
        async with app.state.start_lock:
            previous = app.state.client_requests.get(request.client_request_id)
            if previous is not None:
                return previous
            try:
                problem = await board.call(
                    "create_problem",
                    request_id=request.client_request_id,
                    case_id=request.case_id,
                    title=case["title"],
                    limits={
                        "max_dispatches": settings.max_dispatches,
                        "timeout_seconds": settings.case_timeout_seconds,
                        "max_run_tokens": getattr(settings, "max_run_tokens", 12000),
                    },
                )
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Could not create investigation: {exc}") from exc
            problem_id = str(problem.get("problem_id", ""))
            if not problem_id:
                raise HTTPException(status_code=502, detail="Blackboard returned no problem_id")
            task = asyncio.create_task(runner.run(problem_id), name=f"investigation-{problem_id}")
            app.state.runs[problem_id] = ActiveRun(
                problem_id=problem_id,
                case_id=request.case_id,
                title=case["title"],
                started_at=str(problem.get("created_at", "")),
                task=task,
            )
            response = {
                "problem_id": problem_id,
                "case_id": request.case_id,
                "title": case["title"],
                "status": "investigating",
                "client_request_id": request.client_request_id,
            }
            app.state.client_requests[request.client_request_id] = response
            return response

    @app.get("/runs/{problem_id}")
    async def get_run(problem_id: str) -> dict[str, Any]:
        active: ActiveRun | None = app.state.runs.get(problem_id)
        try:
            snapshot = await board.call("read_blackboard", problem_id=problem_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Could not read investigation: {exc}") from exc
        problem = snapshot.get("problem", {})
        task = active.task if active else None
        result = None
        if task and task.done() and not task.cancelled():
            try:
                result = task.result().as_dict()
            except Exception as exc:
                result = {"status": "blocked", "reason": "controller_task_failed", "error": str(exc)[:300]}
        return {
            "problem_id": problem_id,
            "case_id": problem.get("case_id", active.case_id if active else None),
            "title": problem.get("title", active.title if active else None),
            "status": problem.get("status", "unknown"),
            "board_revision": problem.get("board_revision"),
            "started_at": active.started_at if active else problem.get("created_at"),
            "result": result,
            "model_mode": settings.model_mode,
        }

    @app.get("/blackboard/{problem_id}")
    async def read_blackboard(problem_id: str) -> dict[str, Any]:
        try:
            return await board.call("read_blackboard", problem_id=problem_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Could not read Blackboard: {exc}") from exc

    return app


app = create_controller_app()
