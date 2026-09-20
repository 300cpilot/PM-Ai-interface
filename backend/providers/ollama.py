"""Ollama provider (native /api/chat endpoint)."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from providers.base import Provider


class OllamaProvider(Provider):
    @property
    def base_url(self) -> str:
        return (self.cfg.base_url or "http://127.0.0.1:11434").rstrip("/")

    async def chat(self, messages: list[dict]) -> AsyncIterator[str]:
        payload = {"model": self.cfg.model, "messages": messages, "stream": True}
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                        if content := chunk.get("message", {}).get("content"):
                            yield content
                        if chunk.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue

    async def models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", [])]

    async def health(self) -> tuple[bool, str]:
        try:
            models = await self.models()
            return True, f"ok ({len(models)} models)"
        except Exception as e:
            return False, str(e)
