from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from agentpod.providers.base import (
    CachePricing,
    ChatResponse,
    ModelInfo,
    ModelProvider,
    PricingRule,
)


class VolcEngineProvider(ModelProvider):
    DEFAULT_MODEL = "doubao-seed-1-8-251228"

    def _create_client(self) -> Any:
        return httpx.AsyncClient(
            base_url=self.config.base_url,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(self.config.timeout, connect=10.0),
        )

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
        stream: bool = False,
        **kwargs,
    ) -> ChatResponse | AsyncIterator[dict]:
        model = model or self.config.default_model or self.DEFAULT_MODEL
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
        if "max_completion_tokens" in kwargs:
            body["max_completion_tokens"] = kwargs["max_completion_tokens"]
        if "reasoning_effort" in kwargs:
            body["reasoning_effort"] = kwargs["reasoning_effort"]

        if stream:
            body["stream_options"] = {"include_usage": True}
            return self._stream_chat(body)
        else:
            return await self._non_stream_chat(body)

    async def _non_stream_chat(self, body: dict) -> ChatResponse:
        resp = await self.client.post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]
        usage_raw = data.get("usage", {})

        tool_calls = None
        stop_reason = "end_turn"
        if message.get("tool_calls"):
            stop_reason = "tool_use"
            tool_calls = message["tool_calls"]

        return ChatResponse(
            content=message.get("content", "") or "",
            stop_reason=stop_reason,
            usage={
                "input_tokens": usage_raw.get("prompt_tokens", 0),
                "output_tokens": usage_raw.get("completion_tokens", 0),
                "cached_tokens": usage_raw.get("prompt_tokens_details", {}).get(
                    "cached_tokens", 0
                ),
            },
            model=data.get("model", body["model"]),
            tool_calls=tool_calls,
        )

    async def _stream_chat(self, body: dict) -> AsyncIterator[dict]:
        async for chunk in self._stream_iter(body):
            yield chunk

    async def _stream_iter(self, body: dict) -> AsyncIterator[dict]:
        collected_tool_calls: list[dict] = []
        tool_names_emitted: set[int] = set()
        usage_info: dict = {}
        stop_reason = "end_turn"

        async with self.client.stream(
            "POST", "/chat/completions", json=body
        ) as resp:
            if resp.status_code >= 400:
                error_body = await resp.aread()
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}: {error_body.decode('utf-8', errors='replace')}",
                    request=resp.request,
                    response=resp,
                )
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    break

                chunk = json.loads(payload)
                choices = chunk.get("choices", [])
                choice = choices[0] if choices else {}
                delta = choice.get("delta", {})

                # Reasoning content delta (thinking tokens)
                if delta.get("reasoning_content"):
                    yield {"type": "reasoning_delta", "content": delta["reasoning_content"]}

                # Content delta
                if delta.get("content"):
                    yield {"type": "text_delta", "content": delta["content"]}

                # Tool calls accumulation
                if delta.get("tool_calls"):
                    for tc in delta["tool_calls"]:
                        idx = tc.get("index", 0)
                        while len(collected_tool_calls) <= idx:
                            collected_tool_calls.append(
                                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                            )
                        if tc.get("id"):
                            collected_tool_calls[idx]["id"] = tc["id"]
                        if tc.get("function", {}).get("name"):
                            collected_tool_calls[idx]["function"]["name"] = tc["function"]["name"]
                            if idx not in tool_names_emitted:
                                tool_names_emitted.add(idx)
                                yield {"type": "tool_call_start", "name": tc["function"]["name"]}
                        if tc.get("function", {}).get("arguments"):
                            collected_tool_calls[idx]["function"]["arguments"] += tc["function"]["arguments"]

                if choice.get("finish_reason"):
                    fr = choice["finish_reason"]
                    if fr == "tool_calls":
                        stop_reason = "tool_use"
                        if collected_tool_calls:
                            yield {"type": "tool_use", "tool_calls": list(collected_tool_calls)}
                            collected_tool_calls = []
                    else:
                        stop_reason = "end_turn"

                if chunk.get("usage"):
                    u = chunk["usage"]
                    usage_info = {
                        "input_tokens": u.get("prompt_tokens", 0),
                        "output_tokens": u.get("completion_tokens", 0),
                        "cached_tokens": u.get("prompt_tokens_details", {}).get(
                            "cached_tokens", 0
                        ),
                    }

        if collected_tool_calls:
            yield {"type": "tool_use", "tool_calls": collected_tool_calls}

        yield {"type": "done", "usage": usage_info, "stop_reason": stop_reason}

    async def count_tokens(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
    ) -> int:
        model = model or self.config.default_model or self.DEFAULT_MODEL
        # VolcEngine /tokenization expects a "text" field, not "messages".
        # Serialize messages (and optional tools) into a single text blob.
        parts: list[str] = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and "text" in item:
                        parts.append(item["text"])
        if tools:
            parts.append(json.dumps(tools, ensure_ascii=False))
        text = "\n".join(parts)

        resp = await self.client.post(
            "/tokenization", json={"model": model, "text": text}
        )
        resp.raise_for_status()
        data = resp.json()
        # Response: {"data": [{"total_tokens": N, ...}]}
        token_data = data.get("data", [{}])
        if token_data:
            return token_data[0].get("total_tokens", 0)
        return 0

    def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(
                id="doubao-seed-1-8-251228",
                name="Doubao Seed 1.8",
                context_window=256000,
                pricing_rules=[
                    PricingRule(
                        input_up_to=32000,
                        output_up_to=200,
                        input_price_per_1m=0.8,
                        output_price_per_1m=2.0,
                    ),
                    PricingRule(
                        input_up_to=32000,
                        output_up_to=None,
                        input_price_per_1m=0.8,
                        output_price_per_1m=8.0,
                    ),
                    PricingRule(
                        input_up_to=128000,
                        output_up_to=None,
                        input_price_per_1m=1.2,
                        output_price_per_1m=16.0,
                    ),
                    PricingRule(
                        input_up_to=None,
                        output_up_to=None,
                        input_price_per_1m=2.4,
                        output_price_per_1m=24.0,
                    ),
                ],
                cache_pricing=CachePricing(hit_price_per_1m=0.16),
            )
        ]
