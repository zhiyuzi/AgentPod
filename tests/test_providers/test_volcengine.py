"""Integration tests for VolcEngineProvider using real API calls."""

from __future__ import annotations

import os
from pathlib import Path

import dotenv
import pytest

from agentpod.config import ProviderConfig
from agentpod.providers.volcengine import VolcEngineProvider

# Load .env from project root
dotenv.load_dotenv(Path(__file__).resolve().parents[2] / ".env")

API_KEY = os.environ.get("VOLCENGINE_API_KEY", "")
BASE_URL = os.environ.get("VOLCENGINE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
MODEL = "doubao-seed-1-8-251228"


@pytest.fixture
def provider() -> VolcEngineProvider:
    cfg = ProviderConfig(api_key=API_KEY, base_url=BASE_URL, default_model=MODEL)
    return VolcEngineProvider(cfg)


async def test_chat_non_streaming(provider: VolcEngineProvider):
    messages = [{"role": "user", "content": "说一个字：好"}]
    resp = await provider.chat(messages, stream=False)
    assert resp.content, "Expected non-empty content"
    assert resp.usage.get("input_tokens", 0) > 0
    assert resp.usage.get("output_tokens", 0) > 0
    assert resp.stop_reason == "end_turn"


async def test_chat_streaming(provider: VolcEngineProvider):
    messages = [{"role": "user", "content": "说一个字：好"}]
    chunks: list[dict] = []
    stream = await provider.chat(messages, stream=True)
    async for chunk in stream:
        chunks.append(chunk)

    # Should have at least one text_delta and a done event
    text_chunks = [c for c in chunks if c["type"] == "text_delta"]
    done_chunks = [c for c in chunks if c["type"] == "done"]
    assert len(text_chunks) > 0, "Expected at least one text_delta chunk"
    assert len(done_chunks) == 1, "Expected exactly one done chunk"

    full_content = "".join(c["content"] for c in text_chunks)
    assert full_content, "Expected non-empty streamed content"


async def test_tokenization(provider: VolcEngineProvider):
    messages = [{"role": "user", "content": "你好，世界！Hello, World!"}]
    token_count = await provider.count_tokens(messages)
    assert isinstance(token_count, int)
    assert token_count > 0


async def test_reasoning_effort(provider: VolcEngineProvider):
    messages = [{"role": "user", "content": "1+1=?"}]
    resp = await provider.chat(messages, stream=False, reasoning_effort="minimal")
    assert resp.content, "Expected non-empty content with reasoning_effort=minimal"
    assert resp.stop_reason == "end_turn"
