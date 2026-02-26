"""Preflight checks run before the server starts accepting requests."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CheckResult:
    name: str
    status: str  # "pass", "warn", "fail"
    message: str


async def run_preflight(config) -> list[CheckResult]:
    results: list[CheckResult] = []

    # Create data directory
    data_dir = Path(config.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "users").mkdir(exist_ok=True)
    results.append(CheckResult("data_dir", "pass", f"Data directory: {data_dir}"))

    # Init registry.db
    from agentpod.db import Database

    db = Database(str(data_dir / "registry.db"))
    db.init_db()
    db.close()
    results.append(CheckResult("registry_db", "pass", "registry.db initialized"))

    # Check providers
    from agentpod.config import load_provider_configs

    providers = load_provider_configs()
    if providers:
        results.append(CheckResult("providers", "pass", f"Providers: {', '.join(providers.keys())}"))
    else:
        results.append(CheckResult("providers", "fail", "No provider API keys configured"))

    # Check port
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", config.port))
        sock.close()
        if result == 0:
            results.append(CheckResult("port", "warn", f"Port {config.port} already in use"))
        else:
            results.append(CheckResult("port", "pass", f"Port {config.port} available"))
    except Exception:
        results.append(CheckResult("port", "pass", f"Port {config.port} check skipped"))

    # Check template
    template_dir = data_dir / "template"
    if template_dir.exists() and (template_dir / "AGENTS.md").exists():
        results.append(CheckResult("template", "pass", "template/ directory valid"))
    else:
        results.append(CheckResult("template", "warn", "template/ directory not found (user create will fail)"))

    return results
