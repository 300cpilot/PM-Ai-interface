# proxmox-ai — Task List

## Phase 1 — Backend CT + Skeleton
- [x] 1.1  Provision Debian 13 CT "proxmox-ai" on node01 (192.168.1.XXX, 2c / 2GB / 8GB)
- [x] 1.2  Update CT, install Python 3.12 + venv + git + SQLite
- [x] 1.3  Scaffold `proxmox-ai/backend/` (app.py, config.py, auth.py, permissions.py, providers/, pve_api.py, ssh_exec.py, guest_exec.py, context.py, tasks.py, audit.py, sessions.py)
- [x] 1.4  Create `/etc/proxmox-ai/config.json` (chmod 600) — ALL toggles default false, ssh_mode=off, only local vLLM provider seeded
- [x] 1.5  Implement `auth.py` — validate PVEAuthCookie against /api2/json, require admin perms; settings endpoints require root@pam
- [x] 1.6  Implement `GET /health`
- [x] 1.7  systemd unit `proxmox-ai.service` (port 9000, LAN-only, restart on-failure)
- [ ] 1.8  Verify: health ok with valid PVE session; 401 without

## Phase 2 — LLM Provider Layer
- [x] 2.1  `providers/base.py` — abstract chat(), models(), health()
- [x] 2.2  `providers/vllm.py` (OpenAI-compatible + Bearer) — via `openai_compat.py`
- [x] 2.3  `providers/compatible.py` (generic baseURL + key) — via `openai_compat.py`
- [x] 2.4  `providers/ollama.py` (/api/chat)
- [x] 2.5  `providers/llamacpp.py` — via `openai_compat.py`
- [x] 2.6  `providers/openai.py` — via `openai_compat.py`
- [x] 2.7  `providers/anthropic.py` (messages API)
- [x] 2.8  `providers/openrouter.py` — via `openai_compat.py`
- [x] 2.9  `POST /chat` — route to active provider, stream SSE
- [x] 2.10 `GET/PUT /config` — provider CRUD + active selection; mask API keys
- [x] 2.11 Seed local vLLM provider (192.168.1.XXX:8000/v1, qwen3-30b-a3b, key from vllm.service)
- [x] 2.12 `sessions.py` — SQLite conversation persistence; list/resume/delete endpoints
- [ ] 2.13 Verify: `/chat` streams from local vLLM; session survives reconnect

## Phase 3 — Administration Layer
- [x] 3.1  Create PVE API token (root@pam!proxmox-ai, PVEAdmin) for backend→PVE calls
- [x] 3.2  `pve_api.py` — REST client + typed helpers (nodes, VMs, CTs, storage, network, firewall, backups, users)
- [x] 3.3  `context.py` — read-only cluster snapshot injected into system prompt; refresh command
- [ ] 3.4  `tasks.py` — UPID polling, progress events into chat stream (wired into /chat + `GET /exec/{id}/task` SSE)
- [x] 3.5  Generate per-node SSH keypairs; install pubkeys (node01 first; nodes map for cluster)
- [x] 3.6  `ssh_exec.py` — paramiko + nodes map + approval state machine
- [x] 3.7  `guest_exec.py` — qm agent exec + pct exec wrapper
- [ ] 3.8  Verify: "list VMs" via API; long op shows UPID progress; SSH round-trip to node01

## Phase 4 — Security Model
- [x] 4.1  Master toggles: server_access, internet_access, online_providers, guest_exec
- [x] 4.2  `permissions.py` — per-category matrix (read_status / vmct_lifecycle / storage / network_firewall / users / host_system / guest_exec), each off|confirm|allow
- [x] 4.3  SSH mode: off | confirm | allowlist+confirm | auto; allowlist = read-only verbs
- [x] 4.4  Hard denylist (ALWAYS require approval, any mode): rm -rf, mkfs.*, dd of=, zpool destroy, wipefs, shutdown/poweroff/reboot, destroy --purge, iptables -F, passwd, userdel
- [x] 4.5  Rate limiter (default 10 cmd/min, per-actor) + session max duration (default 2h, enforced in /chat)
- [x] 4.6  Kill switch: /etc/proxmox-ai/DISABLED file + UI toggle → instant 503 on all exec/chat
- [x] 4.7  `audit.py` — append-only JSONL /var/log/proxmox-ai/audit.log (who/what/category/mode/decision/output/UPID) + logrotate
- [x] 4.8  `GET /audit` endpoint
- [ ] 4.9  Verify: full matrix — categories, denylist floor in auto mode, kill switch, rate limit, toggle enforcement

## Phase 5 — UI Patch
- [x] 5.1  `frontend/ai-panel.js` — ExtJS "AI" toolbar button + chat window
- [x] 5.2  Streaming messages + input + session list/resume
- [x] 5.3  Approval cards (command, target node/guest, category + denylist badges, Approve/Deny)
- [x] 5.4  Task progress cards (UPID status) — task_progress SSE events render as cards
- [x] 5.5  `frontend/ai-settings.js` (root@pam only) — master toggles, category matrix grid, SSH mode radio, rate limits, kill switch
- [x] 5.6  Provider editor (type/name/baseURL/apiKey/model/test-connection)
- [x] 5.7  Audit viewer tab
- [x] 5.8  `frontend/patch.sh` — backup + inject into pvemanagerlib.js, statics to /usr/share/pve-manager/js/ai/
- [x] 5.9  dpkg hook `/etc/apt/apt.conf.d/99proxmox-ai-repatch`
- [ ] 5.10 Verify: button visible, chat works, non-admin PVE user gets 403, settings hidden for non-root
- [ ] 5.11 Verify: `apt reinstall pve-manager` → patch re-applied

## Phase 6 — Install + Docs
- [x] 6.1  `install.sh` — one-shot: CT, backend, API token, SSH keys, patch; ends fully locked down except local chat
- [x] 6.2  Uninstall path — `uninstall.sh`: removes patch, dpkg hook, TLS proxy, authorized_keys entry; `DESTROY_CT=1` destroys the CT
- [x] 6.3  `README.md` — architecture, security model, permission matrix reference, uninstall
- [x] 6.4  `docs/ct-setup.md`
- [ ] 6.5  Full end-to-end test from clean state
- [ ] 6.6  Negative tests: no session → 401; non-admin → 403; backend down → graceful UI error; DISABLED file → 503

## Phase 7 — Security Review Findings (2026-09-20)

### Critical
- [x] 7.1  Allowlist bypass via command chaining — FIXED: `SHELL_METACHAR_RE` rejects `;` `|` `&` backticks `$(` `${` `<` `>` newlines before allowlist matching (`permissions.py`)
- [x] 7.2  Kill switch does not stop pending approvals — FIXED: `kill_switch_active()` re-checked at top of `ssh_exec.execute()` (`ssh_exec.py`)
- [x] 7.3  No re-validation at execution time (TOCTOU) — FIXED: `check_execution()` re-run in `execute()`; blocked decisions abort with audit entry (`ssh_exec.py`)

### High
- [x] 7.4  Unquoted command embedding in guest-exec confirm path — FIXED: `shlex.quote()` on guest commands (`guest_exec.py`)
- [x] 7.5  SSH host key verification disabled — FIXED: `RejectPolicy()` + `known_hosts` pinned via `ssh-keyscan` at install (`ssh_exec.py`, `install.sh`, `install-remote.sh`)
- [x] 7.6  TLS verification off by default for PVE API — FIXED: `verify_tls: true` default (`config.py`, `install.sh`, `install-remote.sh`, docs)
- [x] 7.7  Installer grants unrestricted root SSH from CT to host — FIXED: `from="<CT_IP>",restrict` on the authorized_keys entry (`install.sh`, `install-remote.sh`)
- [x] 7.8  Hardcoded secret in repo — FIXED: `VLLM_KEY` default removed from both installers. Rotation deferred (LAN-only test system, key never reached GitHub); `rotate-vllm-key.sh` created for when needed

### Medium
- [x] 7.9  Exec requests not bound to creator — FIXED: status/approve/deny/task endpoints require `req.actor == user.name` (`app.py`)
- [ ] 7.10 Backend listens on `0.0.0.0:9000` plain HTTP — PARTIAL: binding must stay reachable for the host socat proxy; still to do: CT firewall rule limiting `:9000` to the PVE host IP
- [x] 7.11 CORS wide open — FIXED: `allow_origins=[]` (same-origin via PVE proxy), explicit methods/headers (`app.py`)
- [x] 7.12 Prompt-injection → auto-exec path — FIXED: snapshot values sanitized (backticks/newlines stripped, 120-char cap) in `context.py`
- [x] 7.13 Audit log + sessions DB permissions — FIXED: `chmod 600` at creation (`audit.py`, `sessions.py`)

### Low
- [x] 7.14 `session_max_minutes` never enforced — FIXED: enforced in `/chat` via `sessions.get(max_age_minutes=...)` (`sessions.py`, `app.py`)
- [ ] 7.15 Auth ticket cache 60 s TTL — revoked PVE tickets stay valid up to a minute; accepted risk, documented
- [x] 7.16 Global rate limiter — FIXED: per-actor sliding-window buckets (`permissions.py`)
- [x] 7.17 `_requests` dict never expires — FIXED: 1-hour TTL eviction (`ssh_exec.py`)
- [ ] 7.18 `_has_admin_caps` parsing — verify admin detection fails closed for non-root admins (`auth.py`)
- [ ] 7.19 Docs recommend `--privsep 0` root API token — recommend dedicated `proxmox-ai@pve` user with least-privilege role instead (`docs/installation.md`)

## Backlog (v1.1+)
- [ ] B.1  Native tool-calling for vLLM/Qwen3 (Hermes parser)
- [ ] B.2  TLS for backend service
- [ ] B.3  RAG over cluster logs/status history
- [ ] B.4  Per-PVE-user AI permission profiles (beyond admin-only)
- [ ] B.5  Mobile-friendly panel