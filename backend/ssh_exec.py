"""SSH execution with approval state machine. Cluster-ready via nodes map."""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import paramiko

import audit
import permissions
from config import NodeConfig, kill_switch_active, load

# known_hosts for host-key verification (7.5). Override for dev.
KNOWN_HOSTS = os.environ.get("PROXMOX_AI_KNOWN_HOSTS", "/etc/proxmox-ai/keys/known_hosts")

# how long completed/pending requests stay in memory (7.17)
REQUEST_TTL = 3600.0


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
    created_ts: float = field(default_factory=time.monotonic)


# in-flight requests (pending approvals + recent results)
_requests: dict[str, ExecRequest] = {}


def _evict_old() -> None:
    now = time.monotonic()
    for rid in [r for r, q in _requests.items()
                if now - q.created_ts > REQUEST_TTL and q.status != "running"]:
        del _requests[rid]


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
    decision, reason = permissions.check_execution(cfg, command, actor=actor)
    req = ExecRequest(
        id=uuid.uuid4().hex[:12],
        command=command,
        node=node,
        category=permissions.categorize(command),
        decision=decision,
        reason=reason,
        actor=actor,
    )
    _evict_old()
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
    if os.path.exists(KNOWN_HOSTS):
        client.load_host_keys(KNOWN_HOSTS)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        # no known_hosts yet: accept-and-record would be a MITM hole, so refuse
        raise RuntimeError(
            f"known_hosts not found at {KNOWN_HOSTS} — run ssh-keyscan at install time"
        )
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

    # Re-validate at execution time (7.2/7.3): the kill switch, toggles,
    # category modes, and rate limits may have changed since the request
    # was created. A pending approval must not survive a lockdown.
    if kill_switch_active():
        req.status = "blocked"
        req.output = "kill switch active (/etc/proxmox-ai/DISABLED)"
        audit.log("exec_result", actor=req.actor, id=req.id, command=req.command,
                  status="blocked", reason="kill switch active at execution time")
        return req
    cfg = load()
    decision, reason = permissions.check_execution(cfg, req.command, actor=req.actor)
    if decision == "blocked":
        req.status = "blocked"
        req.output = f"blocked at execution time: {reason}"
        audit.log("exec_result", actor=req.actor, id=req.id, command=req.command,
                  status="blocked", reason=reason)
        return req

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
