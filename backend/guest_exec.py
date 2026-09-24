"""In-guest execution: QEMU guest agent via PVE API; LXC via pct exec over SSH.
Gated by the guest_exec toggle + guest_exec category.
"""
from __future__ import annotations

import asyncio
import shlex

import audit
import pve_api
import permissions
import ssh_exec
from config import load


async def qemu_exec(node: str, vmid: int, command: str, actor: str) -> dict:
    cfg = load()
    decision, reason = permissions.check_execution(cfg, f"qm agent {vmid} exec {command}",
                                                   category="guest_exec", actor=actor)
    audit.log("guest_exec_request", actor=actor, kind="qemu", node=node, vmid=vmid,
              command=command, decision=decision, reason=reason)
    if decision == "blocked":
        return {"status": "blocked", "reason": reason}
    if decision == "confirm":
        # quote so the host shell sees the guest command as one literal arg
        req = ssh_exec.request(f"qm agent {vmid} exec -- {shlex.quote(command)}", actor, node)
        return {"status": "confirm", "request_id": req.id, "reason": reason}

    result = await pve_api.qemu_agent_exec(node, vmid, ["/bin/sh", "-c", command])
    pid = result.get("pid")
    for _ in range(60):
        await asyncio.sleep(1)
        status = await pve_api.qemu_agent_exec_status(node, vmid, pid)
        if status.get("exited"):
            out = {
                "status": "done",
                "exit_code": status.get("exitcode"),
                "stdout": status.get("out-data", ""),
                "stderr": status.get("err-data", ""),
            }
            audit.log("guest_exec_result", actor=actor, kind="qemu", vmid=vmid, **out)
            return out
    return {"status": "failed", "reason": "guest agent timeout"}


async def lxc_exec(node: str, vmid: int, command: str, actor: str) -> dict:
    cfg = load()
    full = f"pct exec {vmid} -- {shlex.quote(command)}"
    decision, reason = permissions.check_execution(cfg, full, category="guest_exec",
                                                   actor=actor)
    audit.log("guest_exec_request", actor=actor, kind="lxc", node=node, vmid=vmid,
              command=command, decision=decision, reason=reason)
    if decision == "blocked":
        return {"status": "blocked", "reason": reason}

    req = ssh_exec.request(full, actor, node)
    if decision == "confirm":
        return {"status": "confirm", "request_id": req.id, "reason": reason}

    req.status = "approved"
    req = await ssh_exec.execute(req)
    return {"status": req.status, "exit_code": req.exit_code, "stdout": req.output}
