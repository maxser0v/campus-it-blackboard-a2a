"""Configurable live OpenAI-compatible model calls and an offline rule fixture."""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
import time
from typing import Any, Protocol


class ModelAdapterError(RuntimeError):
    """Provider or adapter failures are surfaced; no model result is fabricated."""


def _was_unavailable_query(context: dict[str, Any], tool: str, query: dict[str, Any]) -> bool:
    """Whether this exact fixture query already returned no records."""
    for item in context.get("unavailable_evidence_queries", []):
        if not isinstance(item, dict) or item.get("tool") != tool:
            continue
        previous = item.get("query")
        if not isinstance(previous, dict):
            continue
        if tool == "query_metrics":
            previous_names = previous.get("metric_names", [])
            query_names = query.get("metric_names", [])
            if isinstance(previous_names, list) and isinstance(query_names, list):
                if sorted(set(previous_names)) == sorted(set(query_names)) and previous.get("contains") == query.get("contains"):
                    return True
        elif previous == query:
            return True
    return False


def _offline_claim_grounded(claim: str, cited_entries: list[dict[str, Any]]) -> bool:
    """Small signal-level check for offline review, including raw record content."""
    records = [
        row
        for entry in cited_entries
        if isinstance(entry.get("data"), dict)
        for row in entry["data"].get("records", [])
        if isinstance(row, dict)
    ]
    if not records:
        return False
    claim_text = claim.lower()
    corpus = json.dumps(records, ensure_ascii=False, sort_keys=True).lower()
    if any(term in claim_text for term in ("database", "pool", "connection")):
        return any(token in corpus for token in ("db_pool", "connection", "pool"))
    if any(term in claim_text for term in ("dns", "resolution", "resolver", "nxdomain")):
        return any(token in corpus for token in ("dns_", "nxdomain", "resolver", "authoritative"))
    claim_tokens = {token for token in re.findall(r"[a-z0-9]+", claim_text) if len(token) > 3}
    corpus_tokens = {token for token in re.findall(r"[a-z0-9]+", corpus) if len(token) > 3}
    return len(claim_tokens & corpus_tokens) >= 2


class ModelAdapter(Protocol):
    mode: str

    async def complete(
        self,
        *,
        role: str,
        purpose: str,
        instructions: str,
        context: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> str: ...


class OpenAICompatibleAdapter:
    """Minimal Chat Completions adapter configured without choosing a provider."""

    mode = "live"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        *,
        timeout: float = 60.0,
        max_output_tokens: int = 1600,
        token_limit_parameter: str | None = "max_tokens",
    ) -> None:
        if not base_url or not model:
            raise ValueError("base_url and model are required for live model mode")
        self.endpoint = self._endpoint(base_url)
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        if token_limit_parameter not in {None, "max_tokens", "max_completion_tokens"}:
            raise ValueError("token_limit_parameter must be max_tokens, max_completion_tokens, or None")
        self.token_limit_parameter = token_limit_parameter
        self.last_call_metadata: dict[str, Any] | None = None

    @staticmethod
    def _endpoint(base_url: str) -> str:
        url = base_url.strip().rstrip("/")
        if url.endswith("/chat/completions"):
            return url
        if url.endswith("/v1"):
            return url + "/chat/completions"
        return url + "/v1/chat/completions"

    async def complete(
        self,
        *,
        role: str,
        purpose: str,
        instructions: str,
        context: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instructions},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"role": role, "purpose": purpose, "context": context, "output_schema": output_schema},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        if self.token_limit_parameter:
            body[self.token_limit_parameter] = self.max_output_tokens
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "campus-blackboard/0.1",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        started = time.perf_counter()

        def send() -> tuple[str, dict[str, Any] | None]:
            try:
                retry_deadline = time.monotonic() + 75
                for attempt in range(5):
                    try:
                        with urllib.request.urlopen(request, timeout=self.timeout) as response:
                            payload = json.loads(response.read().decode("utf-8"))
                        break
                    except urllib.error.HTTPError as error:
                        if error.code not in {400, 429} or attempt == 4:
                            raise
                        detail = error.read(2048).decode("utf-8", errors="replace")
                        if error.code == 400 and 'json_validate_failed' not in detail:
                            raise ModelAdapterError(f"model provider returned HTTP 400: {detail}") from error
                        delay = min(2 ** (attempt + 1), 30)
                        retry_after = error.headers.get("Retry-After") if error.headers else None
                        try:
                            delay = float(retry_after) if retry_after else delay
                        except ValueError:
                            pass
                        match = re.search(r"try again in ([0-9.]+)(ms|s)", detail, re.I)
                        if match:
                            delay = max(delay, float(match[1]) / (1000 if match[2].lower() == "ms" else 1))
                        delay = max(0.1, delay) + 0.25
                        if time.monotonic() + delay > retry_deadline:
                            raise ModelAdapterError(f"model provider rate limit exceeded retry budget: {detail}") from error
                        error.close()
                        time.sleep(delay)
            except urllib.error.HTTPError as error:
                detail = error.read(2048).decode("utf-8", errors="replace")
                raise ModelAdapterError(f"model provider returned HTTP {error.code}: {detail}") from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                raise ModelAdapterError(f"model provider request failed: {error}") from error
            try:
                content = payload["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as error:
                raise ModelAdapterError("model provider response has no chat completion content") from error
            if not isinstance(content, str) or not content.strip():
                raise ModelAdapterError("model provider returned empty or non-text content")
            usage_value = payload.get("usage")
            usage = None
            if isinstance(usage_value, dict):
                usage = {
                    key: value
                    for key, value in usage_value.items()
                    if key in {"prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"}
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                }
            return content, usage

        try:
            content, usage = await asyncio.to_thread(send)
        except Exception:
            self.last_call_metadata = {
                "mode": self.mode,
                "model": self.model,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "usage": None,
            }
            raise
        self.last_call_metadata = {
            "mode": self.mode,
            "model": self.model,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "usage": usage,
        }
        return content


class OfflineFixtureAdapter:
    """Deterministic, non-LLM reasoning aid for local development and demo runs.

    It applies generic rules to fixture observations and never reads answer keys.
    The returned values are marked as offline in each specialist result.
    """

    mode = "offline_fixture"

    def __init__(self) -> None:
        self.last_call_metadata: dict[str, Any] | None = None

    async def complete(
        self,
        *,
        role: str,
        purpose: str,
        instructions: str,
        context: dict[str, Any],
        output_schema: dict[str, Any],
    ) -> str:
        del instructions, output_schema
        started = time.perf_counter()
        result = self._decide(role, purpose, context)
        self.last_call_metadata = {
            "mode": self.mode,
            "model": None,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "usage": None,
        }
        return json.dumps(result, ensure_ascii=False, sort_keys=True)

    def _decide(self, role: str, purpose: str, context: dict[str, Any]) -> dict[str, Any]:
        if purpose == "collect_evidence":
            requests = context.get("open_evidence_requests", [])
            if requests:
                request = requests[0]
                tool = request.get("requested_tool")
                query = request.get("query", {})
                if tool in {"query_metrics", "query_logs", "get_change_history"}:
                    return {"tool": tool, "query": query, "rationale": "Collecting the requested observation to resolve the open evidence question.", "fulfilled_request_entry_id": request.get("entry_id")}
            names = ["request_latency_p95_ms", "request_error_rate_pct", "request_rate_per_min", "app_cpu_pct", "dns_nxdomain_rate_pct"]
            return {"tool": "query_metrics", "query": {"metric_names": names}, "rationale": "Collecting a small baseline of service health measurements.", "fulfilled_request_entry_id": None}

        if purpose == "diagnose":
            entries = context.get("observations", [])
            metrics: dict[str, Any] = {}
            text_rows: list[str] = []
            observation_ids = []
            for entry in entries:
                data = entry.get("data", {})
                observation_ids.append(entry.get("entry_id"))
                for row in data.get("records", []):
                    if "name" in row:
                        metrics[row["name"]] = row.get("value")
                    if "message" in row:
                        text_rows.append(str(row["message"]).lower())
            existing = context.get("own_hypotheses", [])
            if any(
                entry.get("data", {}).get("fulfilled_request_status") == "unavailable"
                for entry in entries
            ):
                prior = next((entry for entry in existing if "available evidence" in str(entry.get("data", {}).get("claim", "")).lower()), None)
                return {"hypotheses": [{"claim": "The available evidence is insufficient to identify the upstream cause", "status": "unresolved", "supporting_evidence_ids": [entry.get("entry_id") for entry in entries if entry.get("data", {}).get("records")], "contradicting_evidence_ids": [], "open_questions": ["The requested targeted measurements were unavailable."], "revise_entry_id": prior.get("entry_id") if prior else None}], "evidence_requests": []}
            pool_active = metrics.get("db_pool_active_connections")
            pool_max = metrics.get("db_pool_max_connections")
            pool_waits = metrics.get("db_pool_wait_count")
            if isinstance(pool_active, (int, float)) and isinstance(pool_max, (int, float)) and pool_max > 0 and pool_active / pool_max >= 0.95 and (pool_waits or 0) > 0:
                supports = [entry.get("entry_id") for entry in entries if any(row.get("name") in {"db_pool_active_connections", "db_pool_max_connections", "db_pool_wait_count"} for row in entry.get("data", {}).get("records", []))]
                prior = next((entry for entry in existing if "connection" in str(entry.get("data", {}).get("claim", "")).lower()), None)
                claim = "Database connection acquisition is constrained by high pool occupancy"
                hypothesis = {"claim": claim, "status": "supported", "supporting_evidence_ids": supports, "contradicting_evidence_ids": [], "open_questions": [], "revise_entry_id": prior.get("entry_id") if prior else None}
                hypotheses = [hypothesis]
                for entry in existing:
                    old = entry.get("data", {})
                    if entry.get("entry_id") != hypothesis["revise_entry_id"] and old.get("status") in {"proposed", "supported"}:
                        hypotheses.append({"claim": old.get("claim", ""), "status": "weakened", "supporting_evidence_ids": old.get("supporting_evidence_ids", []), "contradicting_evidence_ids": supports, "open_questions": [], "revise_entry_id": entry.get("entry_id")})
                return {"hypotheses": hypotheses, "evidence_requests": []}

            dns_value = metrics.get("dns_nxdomain_rate_pct")
            dns_logs = any("nxdomain" in row or ("no address" in row and "lookup" in row) for row in text_rows)
            if isinstance(dns_value, (int, float)) and dns_value >= 10 or dns_logs:
                supports = [entry.get("entry_id") for entry in entries if any(row.get("name") == "dns_nxdomain_rate_pct" for row in entry.get("data", {}).get("records", [])) or any("nxdomain" in str(row.get("message", "")).lower() or "lookup returned no address" in str(row.get("message", "")).lower() for row in entry.get("data", {}).get("records", []))]
                prior = next((entry for entry in existing if "dns" in str(entry.get("data", {}).get("claim", "")).lower()), None)
                return {"hypotheses": [{"claim": "Name resolution for the affected service is failing", "status": "supported", "supporting_evidence_ids": supports, "contradicting_evidence_ids": [], "open_questions": [], "revise_entry_id": prior.get("entry_id") if prior else None}], "evidence_requests": []}

            latency = metrics.get("request_latency_p95_ms")
            error_rate = metrics.get("request_error_rate_pct")
            if isinstance(latency, (int, float)) and (latency > 500 or (isinstance(error_rate, (int, float)) and error_rate > 2)):
                baseline_ids = [entry.get("entry_id") for entry in entries]
                hypotheses = []
                if not any("db_pool_active_connections" in str(entry.get("data", {})) for entry in entries):
                    hypotheses = [
                        {"claim": "A constrained downstream connection resource may be contributing to slow requests", "status": "proposed", "supporting_evidence_ids": baseline_ids, "contradicting_evidence_ids": [], "open_questions": ["What are current active and maximum database pool connections and wait counts?"], "revise_entry_id": None},
                        {"claim": "Elevated incoming demand may be contributing to the observed request failures", "status": "proposed", "supporting_evidence_ids": baseline_ids, "contradicting_evidence_ids": [], "open_questions": ["Are request rate and application CPU elevated during the incident?"], "revise_entry_id": None},
                    ]
                    requests = [{"question": "What are current active and maximum database pool connections and wait counts?", "requested_tool": "query_metrics", "query": {"metric_names": ["db_pool_active_connections", "db_pool_max_connections", "db_pool_wait_count"]}, "reason": "Latency and errors are elevated while the available baseline does not show whether a downstream connection resource is saturated.", "status": "open"}]
                    return {"hypotheses": hypotheses, "evidence_requests": requests}
            if not observation_ids:
                return {"hypotheses": [], "evidence_requests": [{"question": "Can service health measurements and logs be retrieved?", "requested_tool": "query_metrics", "query": {"metric_names": ["request_latency_p95_ms", "request_error_rate_pct"]}, "reason": "No observations are present on the board.", "status": "open"}]}
            return {"hypotheses": [{"claim": "The available observations do not identify a root cause", "status": "unresolved", "supporting_evidence_ids": observation_ids, "contradicting_evidence_ids": [], "open_questions": ["Which upstream dependency is generating the reported 5xx responses?"], "revise_entry_id": None}], "evidence_requests": [{"question": "Are there application dependency, connection-pool, or resolver measurements for the incident interval?", "requested_tool": "query_logs", "query": {"contains": "dependency connection pool resolver upstream"}, "reason": "Current symptom measurements lack a discriminating upstream signal.", "status": "open"}]}

        if role == "review" and purpose in {"review_hypotheses", "review_solution"}:
            target = context.get("target", {})
            data = target.get("data", {})
            if purpose == "review_hypotheses":
                cited = set(data.get("supporting_evidence_ids", []))
                valid = set(context.get("observation_entry_ids", []))
                cited_entries = [entry for entry in context.get("observations", []) if entry.get("entry_id") in cited]
                grounded = _offline_claim_grounded(str(data.get("claim", "")), cited_entries)
                if data.get("status") == "supported" and cited and cited.issubset(valid) and grounded:
                    return {"target_entry_id": target.get("entry_id"), "target_revision": target.get("revision", 1), "verdict": "accept", "rationale": "The current hypothesis cites observations present on this problem board.", "supporting_evidence_ids": sorted(cited), "required_follow_up": None}
                req = context.get("open_evidence_requests", [])
                followup = None
                if req:
                    item = req[0]
                    followup = {key: item.get(key) for key in ("question", "requested_tool", "query", "reason")}
                elif data.get("open_questions"):
                    followup = {"question": data["open_questions"][0], "requested_tool": "query_metrics", "query": {"metric_names": ["db_pool_active_connections", "db_pool_max_connections", "db_pool_wait_count"]}, "reason": "The proposed explanation needs a measurement that distinguishes it from alternatives."}
                if followup and _was_unavailable_query(
                    context,
                    str(followup.get("requested_tool", "")),
                    followup.get("query", {}) if isinstance(followup.get("query"), dict) else {},
                ):
                    followup = None
                return {"target_entry_id": target.get("entry_id"), "target_revision": target.get("revision", 1), "verdict": "request_evidence" if followup else "reject", "rationale": "The claim is not yet supported by decisive observations on the board.", "supporting_evidence_ids": sorted(cited & valid), "required_follow_up": followup}
            citations = set(data.get("evidence_ids", []))
            observation_ids = set(context.get("observation_entry_ids", []))
            runbook_ids = set(context.get("retrieved_runbook_ids", []))
            cites_runbook = set(data.get("runbook_ids", [])).issubset(runbook_ids) and bool(data.get("runbook_ids"))
            cited_entries = [entry for entry in context.get("observations", []) if entry.get("entry_id") in citations]
            ok = bool(data.get("actions")) and citations.issubset(observation_ids) and cites_runbook and bool(cited_entries) and all(entry.get("data", {}).get("records") for entry in cited_entries)
            return {"target_entry_id": target.get("entry_id"), "target_revision": target.get("revision", 1), "verdict": "accept" if ok else "reject", "rationale": "The response cites available observations and retrieved runbook sections." if ok else "The response is missing valid evidence, runbook support, or ordered actions.", "supporting_evidence_ids": sorted(citations & observation_ids), "required_follow_up": None}

        if role == "response" and purpose == "propose_response":
            hypotheses = context.get("supported_hypotheses", [])
            observations = context.get("observations", [])
            evidence_ids = [entry.get("entry_id") for entry in observations]
            runbooks = context.get("runbooks", [])
            chosen = runbooks[0] if runbooks else None
            if not hypotheses or not chosen:
                return {"summary": "Evidence is insufficient for a grounded response proposal.", "actions": [], "caveats": ["No reviewed supported diagnosis and matching runbook are available."], "evidence_ids": evidence_ids, "runbook_ids": [chosen["section_id"]] if chosen else []}
            claim = hypotheses[0].get("data", {}).get("claim", "")
            # This demo policy paraphrases the retrieved runbook's actual checks and steps;
            # the live adapter uses model-selected actions from those same source sections.
            text = chosen.get("text", "")
            first_sentence = next((part.strip() for part in text.split("\n") if part.strip() and not part.startswith("#")), "Verify the reported condition with the service owner.")
            checks = first_sentence[:300]
            return {"summary": f"Investigate the reviewed finding: {claim}.", "actions": [{"step": "Verify the cited measurements and repeat the targeted check over the next sampling interval.", "purpose": "Confirm that the evidence still matches the current service state.", "expected_check": checks}, {"step": "Follow the retrieved runbook's owner-approved diagnostic and recovery sequence.", "purpose": "Keep any intervention within the documented operating procedure.", "expected_check": "The affected service responds normally and the cited error signal falls."}], "caveats": ["This synthetic investigation recommends actions; it does not execute changes.", "Any recovery action requires the responsible service owner."], "evidence_ids": evidence_ids, "runbook_ids": [chosen["section_id"]]}
        return {}


def model_from_settings(settings: Any | None = None) -> ModelAdapter:
    """Build the configured adapter without initiating any request."""
    if settings is None:
        from .config import get_settings
        settings = get_settings()
    mode = getattr(settings, "model_mode", "offline")
    if mode == "offline":
        return OfflineFixtureAdapter()
    if mode != "live":
        raise ValueError("model_mode must be 'offline' or 'live'")
    base_url = getattr(settings, "model_base_url", None) or os.getenv("MODEL_BASE_URL")
    model = getattr(settings, "model_name", None) or os.getenv("MODEL_NAME")
    api_key = getattr(settings, "model_api_key", None) or os.getenv("MODEL_API_KEY")
    if not base_url or not model:
        raise ValueError("live mode requires MODEL_BASE_URL and MODEL_NAME; provider credentials remain optional for compatible local endpoints")
    token_parameter = getattr(settings, "model_token_limit_parameter", None)
    if token_parameter is None:
        token_parameter = os.getenv("CAMPUS_MODEL_TOKEN_LIMIT_PARAMETER", "max_tokens").strip() or None
    return OpenAICompatibleAdapter(
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout=getattr(settings, "model_timeout_seconds", 60.0),
        max_output_tokens=getattr(settings, "model_max_output_tokens", 1600),
        token_limit_parameter=token_parameter,
    )
