"""proxmox-ai backend — FastAPI entry point.

Endpoints:
  GET  /health
  POST /chat                     (SSE stream; parses ```exec blocks)
  GET  /config                   (masked)
  PUT  /config                   (root@pam only)
  POST /providers/test           (test a provider connection)
  POST /exec/request             (propose a command)
  POST /exec/{id}/approve|deny
  GET  /exec/{id}
  POST /guest/qemu/{node}/{vmid} (guest agent exec)
  POST /guest/lxc/{node}/{vmid}  (pct exec)
  GET  /audit
  GET  /sessions, GET/DELETE /sessions/{id}
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import audit
import context
import guest_exec
import permissions
import providers
import pve_api
import sessions
import ssh_exec
import tasks
from auth import User, require_root_dep, require_user
from config import CATEGORIES, Config, get_provider, kill_switch_active, load, masked, save

app = FastAPI(title="proxmox-ai", version="0.1.0")

# The UI is served from the PVE host (:8006) and calls the backend via the
# TLS proxy (:9443) — different port = cross-origin. Allow same-host origins
# on the PVE UI ports; everything else is rejected.
def _cors_origins() -> list[str]:
    cfg = load()
    host = urlparse(cfg.pve.api_url).hostname or "127.0.0.1"
    return [f"https://{host}:8006", f"https://{host}"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["PVEAuthCookie", "CSRFPreventionToken", "Authorization", "Content-Type",
                   "PVE-Auth-Cookie"],
)

EXEC_BLOCK_RE = re.compile(r"```exec(?:\s+node=(\S+))?\s*\n(.*?)```", re.DOTALL)
UPID_RE = re.compile(r"UPID:[^\s:]+:[0-9A-Fa-f]{8}:[0-9A-Fa-f]{8}:[^\s:]+")


async def _stream_task_progress(node: str, output: str):
    """If command output contains a UPID, poll it via tasks.watch() and
    yield SSE-ready dicts. No-op if there's no node or no UPID (8.2/3.4)."""
    if not node:
        return
    m = UPID_RE.search(output or "")
    if not m:
        return
    try:
        async for ev in tasks.watch(node, m.group(0)):
            yield {"event": "task_progress", "data": json.dumps(ev)}
    except Exception as e:
        yield {"event": "task_progress", "data": json.dumps({"state": "error", "detail": str(e)})}


# ---------- models ----------

class ChatIn(BaseModel):
    message: str
    session_id: str | None = None


class ExecIn(BaseModel):
    command: str
    node: str = ""


class GuestExecIn(BaseModel):
    command: str


# ---------- health ----------

@app.get("/health")
async def health():
    return {"ok": True, "kill_switch": kill_switch_active()}


# ---------- chat ----------

@app.post("/chat")
async def chat(body: ChatIn, user: User = Depends(require_user)):
    if kill_switch_active():
        raise HTTPException(503, "proxmox-ai is disabled (kill switch)")
    cfg = load()
    pcfg = get_provider(cfg)
    if not pcfg:
        raise HTTPException(400, "no active provider configured")
    provider = providers.build(pcfg)

    sid = body.session_id or sessions.create(user.name, title=body.message[:60])
    prior = sessions.get(sid, user.name, max_age_minutes=cfg.security.session_max_minutes)
    if prior is None:
        raise HTTPException(404, "session not found or expired")

    messages = [{"role": "system", "content": await context.system_prompt()}]
    messages += prior["messages"]
    messages.append({"role": "user", "content": body.message})
    sessions.append(sid, "user", body.message)
    audit.log("chat", actor=user.name, session=sid, provider=pcfg.id)

    async def stream():
        full = ""
        yield {"event": "session", "data": json.dumps({"session_id": sid})}
        try:
            async for chunk in provider.chat(messages):
                full += chunk
                yield {"event": "delta", "data": json.dumps({"text": chunk})}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)})}
            return

        sessions.append(sid, "assistant", full)

        # command proposals — one request per line, so multi-line blocks get
        # per-command categorization/approval instead of being treated as a
        # single shell blob categorized by its first line
        for m in EXEC_BLOCK_RE.finditer(full):
            node = m.group(1) or ""
            commands = [ln.strip() for ln in m.group(2).strip().splitlines()
                        if ln.strip() and not ln.strip().startswith("#")]
            for command in commands:
                req = ssh_exec.request(command, user.name, node)
                payload = {"id": req.id, "command": command, "node": req.node,
                           "category": req.category, "decision": req.decision,
                           "reason": req.reason, "status": req.status}
                if req.decision == "auto":
                    req = await ssh_exec.execute(req)
                    payload.update({"status": req.status, "output": req.output,
                                    "exit_code": req.exit_code})
                    yield {"event": "exec_result", "data": json.dumps(payload)}
                    async for ev in _stream_task_progress(req.node, req.output):
                        yield ev
                else:
                    yield {"event": "exec_request", "data": json.dumps(payload)}
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(stream())


# ---------- config (settings) ----------

@app.get("/config")
async def get_config(user: User = Depends(require_user)):
    cfg = load()
    data = masked(cfg)
    data["kill_switch"] = kill_switch_active()
    data["categories"] = CATEGORIES
    # built-in default so the Settings UI can show/edit/reset against it
    data["default_system_prompt"] = context.SYSTEM_PROMPT
    return data


@app.put("/config")
async def put_config(cfg: Config, user: User = Depends(require_root_dep)):
    # validate providers against toggles
    for p in cfg.providers:
        if err := permissions.check_provider_allowed(cfg, p.type, p.base_url):
            raise HTTPException(400, f"provider '{p.id}': {err}")
    # preserve masked secrets
    old = load()
    old_keys = {p.id: p.api_key for p in old.providers}
    for p in cfg.providers:
        if p.api_key == "********":
            p.api_key = old_keys.get(p.id, "")
    if cfg.pve.api_token == "********":
        cfg.pve.api_token = old.pve.api_token
    save(cfg)
    audit.log("config_update", actor=user.name)
    return {"ok": True}


class ProviderTestIn(BaseModel):
    type: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""



@app.post("/providers/test")
async def test_provider(body: ProviderTestIn, user: User = Depends(require_root_dep)):
    from config import ProviderConfig
    cfg = load()
    if err := permissions.check_provider_allowed(cfg, body.type, body.base_url):
        raise HTTPException(400, err)
    pcfg = ProviderConfig(id="test", **body.model_dump())
    ok, msg = await providers.build(pcfg).health()
    return {"ok": ok, "message": msg}


# ---------- exec ----------

@app.post("/exec/request")
async def exec_request(body: ExecIn, user: User = Depends(require_user)):
    req = ssh_exec.request(body.command, user.name, body.node)
    out = {"id": req.id, "command": req.command, "node": req.node,
           "category": req.category, "decision": req.decision,
           "reason": req.reason, "status": req.status}
    if req.decision == "auto":
        req = await ssh_exec.execute(req)
        out.update({"status": req.status, "output": req.output, "exit_code": req.exit_code})
    return out


@app.get("/exec/{req_id}")
async def exec_status(req_id: str, user: User = Depends(require_user)):
    req = ssh_exec.get(req_id)
    if not req or req.actor != user.name:
        raise HTTPException(404, "unknown request id")
    return req.__dict__


@app.post("/exec/{req_id}/approve")
async def exec_approve(req_id: str, user: User = Depends(require_user)):
    req = ssh_exec.get(req_id)
    if not req or req.actor != user.name:
        raise HTTPException(404, "unknown request id")
    try:
        ssh_exec.approve(req_id, user.name)
    except ValueError as e:
        raise HTTPException(409, str(e))
    req = await ssh_exec.execute(req)
    return {"status": req.status, "output": req.output, "exit_code": req.exit_code}


@app.post("/exec/{req_id}/deny")
async def exec_deny(req_id: str, user: User = Depends(require_user)):
    req = ssh_exec.get(req_id)
    if not req or req.actor != user.name:
        raise HTTPException(404, "unknown request id")
    try:
        ssh_exec.deny(req_id, user.name)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"status": req.status}


@app.get("/exec/{req_id}/task")
async def exec_task_progress(req_id: str, user: User = Depends(require_user)):
    """SSE stream of UPID task progress for a completed exec request (3.4/5.4)."""
    req = ssh_exec.get(req_id)
    if not req or req.actor != user.name:
        raise HTTPException(404, "unknown request id")

    async def stream():
        async for ev in _stream_task_progress(req.node, req.output):
            yield ev
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(stream())


# ---------- guest exec ----------

@app.post("/guest/qemu/{node}/{vmid}")
async def guest_qemu(node: str, vmid: int, body: GuestExecIn,
                     user: User = Depends(require_user)):
    return await guest_exec.qemu_exec(node, vmid, body.command, user.name)


@app.post("/guest/lxc/{node}/{vmid}")
async def guest_lxc(node: str, vmid: int, body: GuestExecIn,
                    user: User = Depends(require_user)):
    return await guest_exec.lxc_exec(node, vmid, body.command, user.name)


# ---------- audit ----------

@app.get("/audit")
async def get_audit(limit: int = 200, user: User = Depends(require_user)):
    return {"entries": audit.read_tail(limit)}


@app.delete("/audit")
async def clear_audit(user: User = Depends(require_root_dep)):
    audit.clear()
    audit.log("audit_cleared", actor=user.name)
    return {"ok": True}


# ---------- sessions ----------

@app.get("/sessions")
async def list_sessions(user: User = Depends(require_user)):
    return {"sessions": sessions.list_for(user.name)}


@app.get("/sessions/{sid}")
async def get_session(sid: str, user: User = Depends(require_user)):
    s = sessions.get(sid, user.name)
    if not s:
        raise HTTPException(404, "session not found")
    return s


@app.delete("/sessions/{sid}")
async def delete_session(sid: str, user: User = Depends(require_user)):
    if not sessions.delete(sid, user.name):
        raise HTTPException(404, "session not found")
    return {"ok": True}


@app.delete("/sessions")
async def clear_sessions(user: User = Depends(require_user)):
    n = sessions.delete_all(user.name)
    audit.log("sessions_cleared", actor=user.name, count=n)
    return {"ok": True, "deleted": n}


if __name__ == "__main__":
    import uvicorn
    cfg = load()
    uvicorn.run(app, host=cfg.listen_host, port=cfg.listen_port)
