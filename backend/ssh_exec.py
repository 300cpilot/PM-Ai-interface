"""SSH execution with approval state machine. Cluster-ready via nodes map."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import paramiko

import audit
import permissions
from config import NodeConfig, load


@dataclass
class ExecRequest:
    id: str
    command: str
    node: str
    category: str
    decision: str          # blocked | confirm | auto
    reason: str
    actor: str
    status: str = "pending"  # pending | approved | denied | running | done | failed | blocked
    output: str = ""
    exit_code: int | None = None
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# in-flight requests (pending approvals + recent results)
_requests: dict[str, ExecRequest] = {}


def _node_cfg(name: str) -> NodeConfig:
    cfg = load()
    for n in cfg.nodes:
        if n.name == name:
            return n
    if cfg.nodes:
        return cfg.nodes[0]
    raise ValueError("no nodes configured")


def request(command: str, actor: str, node: str = "") -> ExecRequest:
    cfg = load()
    node = node or (cfg.nodes[0].name if cfg.nodes else "node01")
    decision, reason = permissions.check_execution(cfg, command)
    req = ExecRequest(
        id=uuid.uuid4().hex[:12],
        command=command,
        node=node,
        category=permissions.categorize(command),
        decision=decision,
        reason=reason,
        actor=actor,
    )
    _requests[req.id] = req
    audit.log("exec_request", actor=actor, id=req.id, node=node,
              command=command, category=req.category, decision=decision, reason=reason)
    if decision == "blocked":
        req.status = "blocked"
    return req


def get(req_id: str) -> ExecRequest | None:
    return _requests.get(req_id)


def approve(req_id: str, actor: str) -> ExecRequest:
    req = _requests[req_id]
    if req.status != "pending":
        raise ValueError(f"request {req_id} is {req.status}, not pending")
    req.status = "approved"
    audit.log("exec_approved", actor=actor, id=req_id, command=req.command)
    return req


def deny(req_id: str, actor: str) -> ExecRequest:
    req = _requests[req_id]
    if req.status != "pending":
        raise ValueError(f"request {req_id} is {req.status}, not pending")
    req.status = "denied"
    audit.log("exec_denied", actor=actor, id=req_id, command=req.command)
    return req


def _run_ssh(node: NodeConfig, command: str, timeout: int = 120) -> tuple[str, int]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            node.host, port=node.ssh_port, username=node.ssh_user,
            key_filename=node.ssh_key, timeout=15,
        )
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        code = stdout.channel.recv_exit_status()
        return (out + (("\n[stderr]\n" + err) if err else "")), code
    finally:
        client.close()


async def execute(req: ExecRequest) -> ExecRequest:
    """Run an approved (or auto) request. Updates req in place."""
    if req.status not in ("approved",) and req.decision != "auto":
        raise ValueError(f"request {req.id} not approved (status={req.status})")
    req.status = "running"
    try:
        node = _node_cfg(req.node)
        output, code = await asyncio.to_thread(_run_ssh, node, req.command)
        req.output = output
        req.exit_code = code
        req.status = "done" if code == 0 else "failed"
    except Exception as e:
        req.output = str(e)
        req.status = "failed"
    audit.log("exec_result", actor=req.actor, id=req.id, command=req.command,
              status=req.status, exit_code=req.exit_code, output=req.output[:4000])
    return req
