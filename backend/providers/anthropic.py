"""Anthropic provider (messages API with SSE streaming)."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from providers.base import Provider


class AnthropicProvider(Provider):
    BASE = "https://api.anthropic.com/v1"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.cfg.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    async def chat(self, messages: list[dict]) -> AsyncIterator[str]:
        system = ""
        msgs = []
        for m in messages:
            if m["role"] == "system":
                system += m["content"] + "\n"
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        payload = {
            "model": self.cfg.model,
            "max_tokens": 4096,
            "system": system.strip(),
            "messages": msgs,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
            async with client.stream(
                "POST", f"{self.BASE}/messages", headers=self._headers(), json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                        if event.get("type") == "content_block_delta":
                            if text := event.get("delta", {}).get("text"):
                                yield text
                    except json.JSONDecodeError:
                        continue

    async def models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.BASE}/models", headers=self._headers())
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data", [])]

    async def health(self) -> tuple[bool, str]:
        try:
            models = await self.models()
            return True, f"ok ({len(models)} models)"
        except Exception as e:
            return False, str(e)
