from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import dotenv

dotenv.load_dotenv()


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: str = "./data"
    shared_dir: str = ""
    max_concurrent: int = 20
    shutdown_timeout: int = 30
    log_level: str = "info"
    admin_key: str = ""
    webhook_url: str = ""
    webhook_secret: str = ""
    cron_enabled: bool = True
    cron_max_concurrent: int = 5
    cron_tick_interval: int = 60
    cron_sync_interval: int = 300
    sandbox_memory_max: str = ""   # e.g. "256M", empty=no limit
    sandbox_cpu_quota: str = ""    # e.g. "50%", empty=no limit
    sandbox_pids_max: str = ""     # e.g. "64", empty=no limit


@dataclass
class ProviderConfig:
    api_key: str
    base_url: str
    default_model: str = ""
    timeout: int = 60
    max_retries: int = 3


def load_server_config() -> ServerConfig:
    data_dir = os.environ.get("AGENTPOD_DATA_DIR", "./data")
    shared_dir = os.environ.get("AGENTPOD_SHARED_DIR", "")
    if not shared_dir:
        default_shared = Path(data_dir) / "shared"
        if default_shared.is_dir():
            shared_dir = str(default_shared)
    return ServerConfig(
        host=os.environ.get("AGENTPOD_HOST", "0.0.0.0"),
        port=int(os.environ.get("AGENTPOD_PORT", "8000")),
        data_dir=data_dir,
        shared_dir=shared_dir,
        max_concurrent=int(os.environ.get("AGENTPOD_MAX_CONCURRENT", "20")),
        shutdown_timeout=int(os.environ.get("AGENTPOD_SHUTDOWN_TIMEOUT", "30")),
        log_level=os.environ.get("AGENTPOD_LOG_LEVEL", "info"),
        admin_key=os.environ.get("AGENTPOD_ADMIN_KEY", ""),
        webhook_url=os.environ.get("AGENTPOD_WEBHOOK_URL", ""),
        webhook_secret=os.environ.get("AGENTPOD_WEBHOOK_SECRET", ""),
        cron_enabled=os.environ.get("AGENTPOD_CRON_ENABLED", "true").lower() in ("true", "1", "yes"),
        cron_max_concurrent=int(os.environ.get("AGENTPOD_CRON_MAX_CONCURRENT", "5")),
        cron_tick_interval=int(os.environ.get("AGENTPOD_CRON_TICK_INTERVAL", "60")),
        cron_sync_interval=int(os.environ.get("AGENTPOD_CRON_SYNC_INTERVAL", "300")),
        sandbox_memory_max=os.environ.get("AGENTPOD_SANDBOX_MEMORY_MAX", ""),
        sandbox_cpu_quota=os.environ.get("AGENTPOD_SANDBOX_CPU_QUOTA", ""),
        sandbox_pids_max=os.environ.get("AGENTPOD_SANDBOX_PIDS_MAX", ""),
    )


_PROVIDERS: dict[str, str] = {
    "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
    "anthropic": "https://api.anthropic.com",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "minimax": "https://api.minimax.chat",
}


def load_provider_configs() -> dict[str, ProviderConfig]:
    configs: dict[str, ProviderConfig] = {}
    for name, default_base_url in _PROVIDERS.items():
        prefix = name.upper()
        api_key = os.environ.get(f"{prefix}_API_KEY", "")
        if not api_key:
            continue
        base_url = os.environ.get(f"{prefix}_BASE_URL", default_base_url)
        configs[name] = ProviderConfig(api_key=api_key, base_url=base_url)
    return configs
