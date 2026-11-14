from __future__ import annotations

import asyncio
import json
import urllib.error

import pytest

from campus_blackboard.config import Settings
from campus_blackboard.model_adapter import (
    ModelAdapterError,
    OfflineFixtureAdapter,
    OpenAICompatibleAdapter,
    model_from_settings,
)


class FakeHTTPResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        del size
        return json.dumps(self.payload).encode()


def test_live_adapter_is_openai_compatible_and_captures_provider_usage(monkeypatch) -> None:
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data.decode())
        seen["timeout"] = timeout
        return FakeHTTPResponse({
            "choices": [{"message": {"content": '{"choice":"query_metrics"}'}}],
            "usage": {"prompt_tokens": 31, "completion_tokens": 9, "total_tokens": 40, "provider_extra": 7},
        })

    monkeypatch.setattr("campus_blackboard.model_adapter.urllib.request.urlopen", fake_urlopen)
    adapter = OpenAICompatibleAdapter(
        "http://localhost:9999/v1/", "local-model", token_limit_parameter="max_completion_tokens"
    )
    raw = asyncio.run(adapter.complete(
        role="evidence", purpose="collect_evidence", instructions="select a tool",
        context={"tools": ["query_metrics"]}, output_schema={"type": "object"},
    ))

    assert raw == '{"choice":"query_metrics"}'
    assert seen["url"] == "http://localhost:9999/v1/chat/completions"
    assert seen["body"]["model"] == "local-model"
    assert seen["body"]["max_completion_tokens"] == 1600
    assert "Authorization" not in seen["headers"]
    assert adapter.last_call_metadata["usage"] == {
        "prompt_tokens": 31, "completion_tokens": 9, "total_tokens": 40
    }
    assert adapter.last_call_metadata["duration_ms"] >= 0


def test_adapter_does_not_hide_provider_errors_or_fabricate_usage(monkeypatch) -> None:
    def failed_urlopen(*args, **kwargs):
        raise urllib.error.URLError("unavailable")

    monkeypatch.setattr("campus_blackboard.model_adapter.urllib.request.urlopen", failed_urlopen)
    adapter = OpenAICompatibleAdapter("http://localhost:9999", "local-model")
    with pytest.raises(ModelAdapterError, match="request failed"):
        asyncio.run(adapter.complete(
            role="diagnosis", purpose="diagnose", instructions="reason",
            context={}, output_schema={},
        ))
    assert adapter.last_call_metadata["usage"] is None


def test_offline_adapter_is_explicit_and_reports_no_token_counts() -> None:
    adapter = model_from_settings(Settings())
    assert isinstance(adapter, OfflineFixtureAdapter)
    assert adapter.mode == "offline_fixture"
    raw = asyncio.run(adapter.complete(
        role="evidence", purpose="collect_evidence", instructions="",
        context={"open_evidence_requests": []}, output_schema={},
    ))
    assert json.loads(raw)["tool"] == "query_metrics"
    assert adapter.last_call_metadata["mode"] == "offline_fixture"
    assert adapter.last_call_metadata["usage"] is None


def test_live_mode_requires_configuration_but_not_a_network_call() -> None:
    settings = Settings(model_mode="live", model_base_url="http://localhost:9999/v1", model_name="test", model_api_key=None)
    adapter = model_from_settings(settings)
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.endpoint == "http://localhost:9999/v1/chat/completions"
