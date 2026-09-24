"""Proxmox VE REST API client. Preferred path for all structured operations.
Auth: dedicated API token (root@pam!proxmox-ai) — survives UI logouts, revocable.
"""
from __future__ import annotations

from typing import Any

import httpx

from config import load


def _client() -> httpx.AsyncClient:
    cfg = load()
    headers = {}
    if cfg.pve.api_token:
        headers["Authorization"] = f"PVEAPIToken={cfg.pve.api_token}"
    return httpx.AsyncClient(
        base_url=cfg.pve.api_url,
        headers=headers,
        verify=cfg.pve.verify_tls,
        timeout=30.0,
    )


async def get(path: str, **params: Any) -> Any:
    async with _client() as c:
        resp = await c.get(path, params=params or None)
        resp.raise_for_status()
        return resp.json().get("data")


async def post(path: str, **data: Any) -> Any:
    async with _client() as c:
        resp = await c.post(path, data=data or None)
        resp.raise_for_status()
        return resp.json().get("data")


async def delete(path: str) -> Any:
    async with _client() as c:
        resp = await c.delete(path)
        resp.raise_for_status()
        return resp.json().get("data")


# ---- typed helpers ----

async def cluster_resources() -> list[dict]:
    return await get("/cluster/resources")


async def nodes() -> list[dict]:
    return await get("/nodes")


async def node_status(node: str) -> dict:
    return await get(f"/nodes/{node}/status")


async def node_vms(node: str) -> list[dict]:
    return await get(f"/nodes/{node}/qemu")


async def node_cts(node: str) -> list[dict]:
    return await get(f"/nodes/{node}/lxc")


async def node_storage(node: str) -> list[dict]:
    return await get(f"/nodes/{node}/storage")


async def node_ct_templates(node: str) -> list[str]:
    """List available CT template volids (e.g. local:vztmpl/debian-13-...tar.zst)."""
    out: list[str] = []
    for st in await node_storage(node):
        if "vztmpl" not in (st.get("content") or ""):
            continue
        try:
            for item in await get(f"/nodes/{node}/storage/{st['storage']}/content", content="vztmpl"):
                out.append(item.get("volid", ""))
        except Exception:
            continue
    return [v for v in out if v]


async def node_tasks(node: str, limit: int = 20) -> list[dict]:
    return await get(f"/nodes/{node}/tasks", limit=limit)


async def task_status(node: str, upid: str) -> dict:
    return await get(f"/nodes/{node}/tasks/{upid}/status")


async def task_log(node: str, upid: str, limit: int = 50) -> list[dict]:
    return await get(f"/nodes/{node}/tasks/{upid}/log", limit=limit)


async def vm_action(node: str, vmid: int, action: str) -> str:
    """start/stop/shutdown/reboot/reset/suspend/resume -> returns UPID."""
    return await post(f"/nodes/{node}/qemu/{vmid}/status/{action}")


async def ct_action(node: str, vmid: int, action: str) -> str:
    return await post(f"/nodes/{node}/lxc/{vmid}/status/{action}")


async def qemu_agent_exec(node: str, vmid: int, command: list[str]) -> dict:
    return await post(f"/nodes/{node}/qemu/{vmid}/agent/exec", command=command)


async def qemu_agent_exec_status(node: str, vmid: int, pid: int) -> dict:
    return await get(f"/nodes/{node}/qemu/{vmid}/agent/exec-status", pid=pid)
