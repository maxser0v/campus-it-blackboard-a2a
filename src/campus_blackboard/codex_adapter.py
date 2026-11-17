"""Non-interactive Codex CLI model backend using the user's existing login."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile
import time
from typing import Any

from .model_adapter import ModelAdapterError


class CodexCLIAdapter:
    mode = "live"

    def __init__(self, executable: str = "codex", model: str | None = None, timeout: float = 180, reasoning_effort: str | None = None):
        self.executable = executable
        self.model = model
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort
        self.last_call_metadata: dict[str, Any] | None = None

    async def complete(self, *, role: str, purpose: str, instructions: str,
                       context: dict[str, Any], output_schema: dict[str, Any]) -> str:
        started = time.perf_counter()
        self.last_call_metadata = {"mode": "live", "provider": "codex_cli", "model": self.model or "codex-default", "usage": None}
        executable = shutil.which(self.executable)
        if not executable:
            raise ModelAdapterError("Codex CLI was not found. Install it or set CAMPUS_CODEX_EXECUTABLE.")
        # This envelope is a real strict JSON Schema. The existing specialist
        # schemas describe role contracts in examples, so they cannot be passed
        # directly to --output-schema. Runtime validators still check the result.
        envelope = {"type": "object", "properties": {"result": {"type": "string"}},
                    "required": ["result"], "additionalProperties": False}
        prompt = (
            "You are a reasoning-only component in a synthetic IT investigation. "
            "Use only the supplied context. Do not inspect files, run commands, browse, "
            "or use other tools. Return the required role JSON object serialized as the "
            "string in the result field of the output envelope.\n\n"
            + instructions + "\n\n" + json.dumps({"role": role, "purpose": purpose,
                "context": context, "output_schema": output_schema}, ensure_ascii=False)
        )
        # Keep login discovery but do not pass campus/Groq/API secrets to the CLI.
        environment = {k: v for k, v in os.environ.items() if k in {
            "PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "CODEX_HOME", "LANG", "LC_ALL",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
        }}
        try:
            with tempfile.TemporaryDirectory(prefix="campus-codex-") as directory:
                root = Path(directory)
                schema = root / "schema.json"
                output = root / "response.json"
                schema.write_text(json.dumps(envelope), encoding="utf-8")
                command = [executable, "exec", "--ignore-user-config", "--ephemeral",
                    "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
                    "--json", "--output-schema", str(schema), "--output-last-message", str(output),
                    "-c", 'web_search="disabled"',
                    "--disable", "shell_tool", "--disable", "unified_exec",
                    "--disable", "multi_agent", "--disable", "plugins", "--disable", "apps",
                    "--disable", "hooks", "--disable", "memories", "--disable", "image_generation",
                    "--disable", "browser_use", "--disable", "computer_use"]
                if self.model:
                    command += ["--model", self.model]
                if self.reasoning_effort:
                    command += ["-c", "model_reasoning_effort=" + json.dumps(self.reasoning_effort)]
                command += ["-"]
                process = await asyncio.create_subprocess_exec(*command, cwd=directory,
                    env=environment, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, start_new_session=True)
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(prompt.encode()), self.timeout)
                except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.communicate()
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    raise ModelAdapterError(f"Codex CLI timed out after {self.timeout:g} seconds.") from exc
                if process.returncode:
                    # No raw CLI diagnostics: they can contain user configuration.
                    raise ModelAdapterError(f"Codex CLI exited with status {process.returncode}. Check `codex login status` and model access.")
                usage = None
                for line in stdout.decode(errors="replace").splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") in {"thread.failed", "turn.failed", "error"}:
                        raise ModelAdapterError("Codex CLI reported a failed turn. Check login, model access, and usage limits.")
                    if event.get("type") == "turn.completed":
                        raw_usage = event.get("usage") or {}
                        inp, out = raw_usage.get("input_tokens"), raw_usage.get("output_tokens")
                        if all(type(value) is int and value >= 0 for value in (inp, out)):
                            usage = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
                if not output.is_file():
                    raise ModelAdapterError("Codex CLI returned no final response.")
                try:
                    result = json.loads(output.read_text(encoding="utf-8"))["result"]
                    if not isinstance(result, str) or not isinstance(json.loads(result), dict):
                        raise ValueError("expected a serialized JSON object")
                except (ValueError, KeyError, TypeError) as exc:
                    raise ModelAdapterError("Codex CLI returned an invalid JSON result.") from exc
                self.last_call_metadata["usage"] = usage
                return result
        except OSError as exc:
            raise ModelAdapterError("Codex CLI could not be started. Check CAMPUS_CODEX_EXECUTABLE and local permissions.") from exc
        finally:
            self.last_call_metadata["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
