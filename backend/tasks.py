"""PVE UPID task polling. Long-running ops report progress into the chat stream."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pve_api


async def watch(node: str, upid: str, poll: float = 2.0, timeout: float = 1800.0) -> AsyncIterator[dict]:
    """Yield task status dicts until the task stops or timeout."""
    elapsed = 0.0
    while elapsed < timeout:
        try:
            status = await pve_api.task_status(node, upid)
        except Exception as e:
            yield {"state": "error", "detail": str(e)}
            return
        state = status.get("status", "unknown")
        if state == "stopped":
            yield {
                "state": "stopped",
                "exitstatus": status.get("exitstatus", "?"),
                "upid": upid,
            }
            return
        yield {"state": state, "upid": upid, "progress": status.get("progress")}
        await asyncio.sleep(poll)
        elapsed += poll
    yield {"state": "timeout", "upid": upid}
