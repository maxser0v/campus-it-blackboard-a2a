"""Framework integration checks using local doubles, never a live provider."""

import asyncio
import json

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import BaseTool

from campus_blackboard.langchain_adapter import CampusChatModel, specialist_chain
from campus_blackboard.tools import ToolRegistry


class Events(BaseCallbackHandler):
    def __init__(self):
        self.models = 0
        self.tools = 0

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.models += 1

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.tools += 1


@pytest.mark.asyncio
async def test_chain_preserves_provider_contract_and_emits_langchain_callbacks():
    class Backend:
        last_call_metadata = {"total_tokens": 17, "provider": "local-double"}

        async def complete(self, **request):
            self.request = request
            return '{"claim": "example"}'

    backend = Backend()
    events = Events()
    request = {"role": "diagnosis", "purpose": "diagnose", "context": {"entries": []},
               "output_schema": {"type": "object"}}
    raw = await specialist_chain(backend).ainvoke(
        {"instructions": "Use {only} observed evidence.", "task": json.dumps(request)},
        config={"callbacks": [events]},
    )
    assert json.loads(raw) == {"claim": "example"}
    assert backend.request == {**request, "instructions": "Use {only} observed evidence."}
    assert events.models == 1
    message = await CampusChatModel(backend=backend).ainvoke([
        ("system", "Use evidence"), ("human", json.dumps(request)),
    ])
    assert message.response_metadata["total_tokens"] == 17
    assert message.usage_metadata is None  # No fabricated input/output breakdown.
    assert "backend" not in CampusChatModel(backend=backend).model_dump()


@pytest.mark.asyncio
async def test_chain_cancellation_reaches_provider():
    cancelled = asyncio.Event()

    class Backend:
        async def complete(self, **request):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

    task = asyncio.create_task(specialist_chain(Backend()).ainvoke({
        "instructions": "test", "task": json.dumps({"role": "review", "purpose": "review_solution",
                                                     "context": {}, "output_schema": {}}),
    }))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


def test_registry_exposes_executable_langchain_tools_with_provenance():
    registry = ToolRegistry()
    assert all(isinstance(item, BaseTool) for item in registry.langchain_tools.values())
    events = Events()
    result = registry.langchain_tools["query_metrics"].invoke({
        "case_id": "db_pool", "metric_names": ["db_pool_active_connections"],
    }, config={"callbacks": [events]})
    assert result["records"][0]["value"] == 40
    assert result["source_ids"]
    assert events.tools == 1
    runbooks = registry.execute("search_runbooks", arguments={"query": "database connection pool"})
    assert runbooks["results"][0]["runbook_id"] == "RB-DB-POOL-01"
    with pytest.raises(ValueError):
        registry.execute("query_logs", case_id="db_pool", arguments={"case_id": "dns_fault"})
    with pytest.raises(ValueError):
        registry.langchain_tools["query_logs"].invoke({"case_id": "db_pool", "limit": True})
    with pytest.raises(ValueError):
        registry.langchain_tools["query_metrics"].invoke({"case_id": "db_pool", "shell": "anything"})
