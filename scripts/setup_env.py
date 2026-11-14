"""Create a private local .env with unique service tokens when none exists."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
TEMPLATE_PATH = ROOT / ".env.example"
ACTORS = ("controller", "dashboard", "evidence", "diagnosis", "review", "response")


def initialize_env(env_path: Path = ENV_PATH, template_path: Path = TEMPLATE_PATH) -> bool:
    """Write generated credentials only when the target does not exist."""
    if env_path.exists():
        return False
    template = template_path.read_text(encoding="utf-8")
    credentials = json.dumps({actor: secrets.token_urlsafe(32) for actor in ACTORS}, separators=(",", ":"))
    lines = template.splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith("CAMPUS_AGENT_CREDENTIALS_JSON="):
            lines[index] = f"CAMPUS_AGENT_CREDENTIALS_JSON={credentials}"
            replaced = True
            break
    if not replaced:
        raise ValueError(".env.example is missing CAMPUS_AGENT_CREDENTIALS_JSON")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines) + "\n"
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
    except Exception:
        env_path.unlink(missing_ok=True)
        raise
    return True


def main() -> int:
    created = initialize_env()
    if created:
        print("Created .env with unique local service credentials and offline model mode.")
        print("The generated file is private and is not overwritten by this script.")
    else:
        print(".env already exists; left it unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
