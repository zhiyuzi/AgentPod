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

    # Check cron
    if config.cron_enabled:
        try:
            import croniter  # noqa: F401
            results.append(CheckResult("cron", "pass", f"Cron enabled (tick={config.cron_tick_interval}s, max_concurrent={config.cron_max_concurrent})"))
        except ImportError:
            results.append(CheckResult("cron", "fail", "Cron enabled but croniter not installed"))
    else:
        results.append(CheckResult("cron", "pass", "Cron disabled"))

    # Check shared directory
    _shared_dir_str = getattr(config, "shared_dir", "") or ""
    shared_dir = Path(_shared_dir_str) if _shared_dir_str else None
    if shared_dir is None:
        # Try default path
        default_shared = data_dir / "shared"
        if default_shared.is_dir():
            shared_dir = default_shared

    if shared_dir is not None:
        if not shared_dir.is_dir():
            results.append(CheckResult("shared", "warn", f"shared/ configured but not found: {shared_dir}"))
        else:
            agents_md = (shared_dir / "AGENTS.md").exists()
            skills_dir = shared_dir / ".agents" / "skills"
            skill_count = len([d for d in skills_dir.iterdir() if d.is_dir()]) if skills_dir.is_dir() else 0

            if not agents_md and skill_count == 0:
                results.append(CheckResult("shared", "warn", "shared/ exists but empty"))
            else:
                parts = []
                if agents_md:
                    parts.append("AGENTS.md")
                parts.append(f"{skill_count} skills")
                results.append(CheckResult("shared", "pass", f"shared/ valid ({', '.join(parts)})"))
    else:
        results.append(CheckResult("shared", "pass", "shared/ not found (shared layer disabled)"))

    return results
