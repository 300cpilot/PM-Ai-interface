## Plan: Proxmox VE AI Assistant Button (proxmox-ai)

TL;DR: Add an "AI Assistant" button + chat panel to the Proxmox VE 9.2 web UI (node01.example.com, 192.168.1.XXX:8006) by patching pve-manager ExtJS directly (same approach as the existing subscription-popup patch). A dedicated backend CT ("proxmox-ai") brokers all LLM traffic (local vLLM/ollama/llama.cpp + online OpenAI/Anthropic/OpenRouter/compatible), administrates the host AND cluster via the PVE REST API (preferred) + SSH fallback, injects live cluster context into the LLM, enforces granular per-category permissions with a hard safety floor, and keeps an append-only audit log. Goal: full administration capability for a home user, lockable down to read-only (or fully off) by the admin.

**User decisions (2026-09-19)**
- Integration: patch pve-manager JS directly (re-apply after updates via dpkg hook)
- Backend: dedicated CT/VM (new "proxmox-ai" CT, NOT co-located with vLLM CT 100)
- SSH safety: user-selectable at runtime — confirm-each / allowlist+confirm / full-auto
- Providers: local vLLM, ollama, llama.cpp + OpenAI, Anthropic, OpenRouter, generic OpenAI-compatible
- Auth: tie to PVE login — validate PVEAuthCookie/ticket; only PVE admins use AI; settings locked to root@pam
- Guest exec: included in v1 (qm agent exec / pct exec) behind its own toggle, default off
- Denylist: destructive commands ALWAYS require approval, even in full-auto mode

**Infrastructure facts (from workspace docs)**
- PVE host: node01.example.com, 192.168.1.XXX:8006, PVE 9.2.2, Debian 13, root SSH available
- PVE API: https://192.168.1.XXX:8006/api2/json (ticket auth → PVEAuthCookie + CSRFPreventionToken)
- Local vLLM: CT 100, 192.168.1.XXX:8000/v1, model `qwen3-30b-a3b`, Bearer key required, tool-calling enabled
- Secondary vLLM: 192.168.1.XXX:8080/v1 (no auth, LAN)
- Existing patch precedent: subscription popup disabled in proxmoxlib.js — same patching mechanism to reuse

**Steps**

Phase 1 — Backend CT + skeleton (blocks all later phases)
1. Provision Debian 13 CT "proxmox-ai" (192.168.1.XXX, 2 cores / 2GB / 8GB) via `pct` on node01; document in `proxmox-ai/docs/ct-setup.md`
2. Scaffold FastAPI backend in `proxmox-ai/backend/` (app.py, config.py, auth.py, providers/, pve_api.py, ssh_exec.py, guest_exec.py, context.py, tasks.py, audit.py, sessions.py); systemd unit; LAN-only bind
3. PVE auth integration (`auth.py`): UI sends PVEAuthCookie + CSRF token with requests; backend validates ticket against https://192.168.1.XXX:8006/api2/json and checks user has admin-equivalent perms (PVEAdmin or root@pam); settings endpoints require root@pam; no separate token to manage

Phase 2 — LLM provider layer (parallel with Phase 4)
4. Provider abstraction: `providers/base.py` (chat(), models(), health()); implementations: vllm, ollama, llamacpp, openai, anthropic, openrouter, compatible (generic baseURL+key)
5. `POST /chat` proxy: routes to active provider, streams SSE; `GET/PUT /config` for providers + active selection; API keys in `/etc/proxmox-ai/config.json` (chmod 600), masked in responses
6. Conversation persistence (`sessions.py`): chat history stored server-side (SQLite at /var/lib/proxmox-ai/sessions.db), survives panel close; list/resume/delete sessions from UI

Phase 3 — Administration layer (depends on 1, 3)
7. `pve_api.py`: PVE REST client (ticket or API-token auth); typed helpers for nodes, VMs, CTs, storage, network, firewall, backups, users; ALL structured ops prefer API over SSH
8. `context.py`: read-only cluster snapshot (node status, VM/CT list + state, storage usage, network config, recent tasks) auto-injected into system prompt each session; refresh on demand
9. `tasks.py`: PVE UPID task polling — long ops (migrate, clone, backup) report progress/completion into the chat stream
10. `ssh_exec.py`: paramiko, `nodes` map (node01 first; cluster nodes config-only to add); per-node keypairs installed by setup script
11. `guest_exec.py`: `qm agent exec` (QEMU guest agent) + `pct exec` (LXC) — behind `guest_exec` toggle, default off

Phase 4 — Security model (depends on 3; the "locked down" core)
12. Master toggles: `server_access` (all execution off when false), `internet_access` (reject non-RFC1918 provider baseURLs), `online_providers` (local types only), `guest_exec` (in-guest commands)
13. Per-category permission matrix (each: off | confirm | allow | and ssh_mode applies to raw shell): read_status / vmct_lifecycle / storage / network_firewall / users / host_system / guest_exec
14. SSH mode: off | confirm | allowlist+confirm | auto (allowlist = read-only verbs: pvesh get, qm list, pct list/status, df, free, systemctl status, journalctl -n)
15. Hard denylist (ALWAYS requires explicit approval regardless of mode): rm -rf, mkfs.*, dd of=, zpool destroy, wipefs, shutdown/poweroff/reboot, pct destroy / qm destroy with --purge, iptables -F, passwd, userdel
16. Rate limits + session guardrails: max N commands/min (default 10), max session duration (default 2h), configurable
17. Emergency kill switch: presence of `/etc/proxmox-ai/DISABLED` file (or UI toggle) instantly 503s all exec/chat endpoints; checked per-request
18. Audit (`audit.py`): append-only JSONL at /var/log/proxmox-ai/audit.log (who, what, category, mode, decision, output, task UPID); logrotate config; `GET /audit` + audit viewer tab in UI

Phase 5 — UI patch (parallel with Phase 2)
19. `frontend/ai-panel.js`: ExtJS "AI" toolbar button + chat window (streaming, session list, task progress cards, approval cards with Approve/Deny, category + denylist badges on proposed commands)
20. `frontend/ai-settings.js` (root@pam only): master toggles, per-category permission matrix grid, SSH mode radio, rate limits, kill switch, provider editor (type/name/baseURL/apiKey/model/test), audit viewer tab
21. `frontend/patch.sh`: idempotent patcher (backup + inject into pvemanagerlib.js, statics to /usr/share/pve-manager/js/ai/); dpkg post-invoke hook `/etc/apt/apt.conf.d/99proxmox-ai-repatch`

Phase 6 — Install + verification
22. `install.sh`: one-shot — CT, backend, service, per-node SSH keys, patch, initial config (everything default-OFF except local vLLM chat)
23. Verification (below); `README.md` (architecture, security model, config reference, uninstall); `docs/ct-setup.md`

**Relevant files**
- `proxmox-ai/backend/app.py` — FastAPI entry: /chat, /config, /exec/*, /guest/*, /audit, /sessions, /health
- `proxmox-ai/backend/auth.py` — PVE ticket validation + admin check (pattern: forward cookie to /api2/json/version)
- `proxmox-ai/backend/pve_api.py` — PVE REST client; preferred path for all structured ops
- `proxmox-ai/backend/ssh_exec.py` — paramiko + nodes map + allowlist/denylist matchers + approval state machine
- `proxmox-ai/backend/guest_exec.py` — qm agent exec / pct exec wrapper
- `proxmox-ai/backend/context.py` — cluster snapshot → system prompt injection
- `proxmox-ai/backend/tasks.py` — UPID polling → SSE progress events
- `proxmox-ai/backend/permissions.py` — category matrix + denylist + rate limiter + kill-switch check
- `proxmox-ai/backend/providers/*.py` — 7 provider implementations
- `proxmox-ai/frontend/ai-panel.js`, `ai-settings.js`, `patch.sh` + dpkg hook
- `proxmox-ai/install.sh`, `README.md`, `docs/ct-setup.md`
- Reference: `vllm.service`, `connections.md` (auth flow), `OPENCODE-VLLM.md`

**Verification**
1. Health + chat: `/health` ok; `/chat` streams from local vLLM with live cluster context in system prompt
2. Auth: valid PVE admin session → panel works; non-admin PVE user → 403; no session → 401; settings tab hidden for non-root@pam
3. API path: "list VMs" answered via pve_api (not SSH); "create CT" issues correct API call + UPID task progress appears in chat
4. Permission matrix: vmct_lifecycle=off → VM start rejected; =confirm → approval card; =allow → executes
5. Denylist floor: full-auto mode + "rm -rf /var/lib/vz" → STILL requires approval; "zpool destroy rpool" → approval
6. Kill switch: `touch /etc/proxmox-ai/DISABLED` → all endpoints 503 instantly; remove → recovers
7. Rate limit: 11th command in a minute → 429 with chat message
8. Guest exec: toggle off → pct exec rejected; on + confirm → approval card shows target guest + command
9. Toggle matrix: internet_access=off → OpenAI baseURL rejected; online_providers=off → only local types
10. Audit: every action (approved/denied/auto/blocked) present in audit.log + visible in UI audit tab; logrotate active
11. Patch persistence: `apt reinstall pve-manager` → hook re-applies, button survives
12. Sessions: close panel, reopen → prior conversation resumable

**Decisions / scope**
- Included: chat, 7 providers, PVE API + SSH + guest exec, live context injection, task tracking, per-category permissions, denylist floor, rate limits, kill switch, append-only audit + UI viewer, session persistence, PVE-auth integration, patch persistence
- Excluded (v1): TLS for backend (LAN-only), RAG over logs, mobile UI, native tool-calling (prompt-based proposals v1; Hermes tool-calling v1.1)
- Secrets: provider keys only in backend config (chmod 600); never in UI/localStorage
- Backend port: 9000; default posture: everything OFF except local vLLM chat (admin opts into power)

**Further considerations**
1. Cluster nodes: `nodes` map in ssh_exec + pve_api cluster endpoints handle multi-node from day one; adding a node = config entry + SSH key install.
2. API token vs ticket for backend→PVE: recommend a dedicated API token (root@pam!proxmox-ai) with PVEAdmin — survives UI logouts, revocable independently.