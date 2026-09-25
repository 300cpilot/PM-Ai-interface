# proxmox-ai — Task List

## Phase 1 — Backend CT + Skeleton
- [x] 1.1  Provision Debian 13 CT "proxmox-ai" on node01 (192.168.1.XXX, 2c / 2GB / 8GB)
- [x] 1.2  Update CT, install Python 3.12 + venv + git + SQLite
- [x] 1.3  Scaffold `proxmox-ai/backend/` (app.py, config.py, auth.py, permissions.py, providers/, pve_api.py, ssh_exec.py, guest_exec.py, context.py, tasks.py, audit.py, sessions.py)
- [x] 1.4  Create `/etc/proxmox-ai/config.json` (chmod 600) — ALL toggles default false, ssh_mode=off, only local vLLM provider seeded
- [x] 1.5  Implement `auth.py` — validate PVEAuthCookie against /api2/json, require admin perms; settings endpoints require root@pam
- [x] 1.6  Implement `GET /health`
- [x] 1.7  systemd unit `proxmox-ai.service` (port 9000, LAN-only, restart on-failure)
- [ ] 1.8  Verify: health ok with valid PVE session; 401 without — PARTIAL: confirmed 401 with no cookie on `/config`, `/audit`, `/sessions`, `/chat` (2026-09-25); the "ok with a valid session" case still needs a live browser login to confirm end to end

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
- [x] 3.4  `tasks.py` — UPID polling, progress events into chat stream (wired into /chat + `GET /exec/{id}/task` SSE) — FIXED 2026-09-25: `_stream_task_progress()` was referenced but never defined (see 8.2); now implemented in `app.py` using `tasks.watch()`, deployed and health-checked
- [x] 3.5  Generate per-node SSH keypairs; install pubkeys (node01 first; nodes map for cluster)
- [x] 3.6  `ssh_exec.py` — paramiko + nodes map + approval state machine
- [x] 3.7  `guest_exec.py` — qm agent exec + pct exec wrapper
- [ ] 3.8  Verify: "list VMs" via API; long op shows UPID progress; SSH round-trip to node01 — code path now unblocked by 3.4's fix; still needs a live run with a real session to confirm end to end

## Phase 4 — Security Model
- [x] 4.1  Master toggles: server_access, internet_access, online_providers, guest_exec
- [x] 4.2  `permissions.py` — per-category matrix (read_status / vmct_lifecycle / storage / network_firewall / users / host_system / guest_exec), each off|confirm|allow
- [x] 4.3  SSH mode: off | confirm | allowlist+confirm | auto; allowlist = read-only verbs
- [x] 4.4  Hard denylist (ALWAYS require approval, any mode): rm -rf, mkfs.*, dd of=, zpool destroy, wipefs, shutdown/poweroff/reboot, destroy --purge, iptables -F, passwd, userdel
- [x] 4.5  Rate limiter (default 10 cmd/min, per-actor) + session max duration (default 2h, enforced in /chat)
- [x] 4.6  Kill switch: /etc/proxmox-ai/DISABLED file + UI toggle → instant 503 on all exec/chat
- [x] 4.7  `audit.py` — append-only JSONL /var/log/proxmox-ai/audit.log (who/what/category/mode/decision/output/UPID) + logrotate
- [x] 4.8  `GET /audit` endpoint
- [ ] 4.9  Verify: full matrix — categories, denylist floor in auto mode, kill switch, rate limit, toggle enforcement — PARTIAL: kill switch confirmed end to end (`/health.kill_switch` flips true/false with the DISABLED file, 2026-09-25); category matrix, denylist floor, and rate limit still need a live authenticated run

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
- [ ] 6.6  Negative tests: no session → 401; non-admin → 403; backend down → graceful UI error; DISABLED file → 503 — PARTIAL: confirmed 2026-09-25 — no session gives 401 on `/config`/`/audit`/`/sessions`/`/chat`; stopped backend gives connection-refused (frontend's `.catch()` already renders "Connection error: ..."). Still needs: a non-admin PVE login (403) and a valid-session request while DISABLED is set (503) — both require a real PVE user session

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
- [x] 7.10 Backend listens on `0.0.0.0:9000` plain HTTP — binding stays reachable for the host socat proxy; CT-local firewall rule now restricts `:9000` to the PVE host IP only — see 8.5
- [x] 7.11 CORS wide open — FIXED: `allow_origins=[]` (same-origin via PVE proxy), explicit methods/headers (`app.py`)
- [x] 7.12 Prompt-injection → auto-exec path — FIXED: snapshot values sanitized (backticks/newlines stripped, 120-char cap) in `context.py`
- [x] 7.13 Audit log + sessions DB permissions — FIXED: `chmod 600` at creation (`audit.py`, `sessions.py`)

### Low
- [x] 7.14 `session_max_minutes` never enforced — FIXED: enforced in `/chat` via `sessions.get(max_age_minutes=...)` (`sessions.py`, `app.py`)
- [ ] 7.15 Auth ticket cache 60 s TTL — revoked PVE tickets stay valid up to a minute; accepted risk, documented
- [x] 7.16 Global rate limiter — FIXED: per-actor sliding-window buckets (`permissions.py`)
- [x] 7.17 `_requests` dict never expires — FIXED: 1-hour TTL eviction (`ssh_exec.py`)
- [x] 7.18 `_has_admin_caps` parsing — VERIFIED: fails closed, but is broken for its intended purpose — see 8.3
- [x] 7.19 Docs recommend `--privsep 0` root API token — FIXED 2026-09-25: `docs/installation.md` Step 4 now leads with a dedicated `proxmox-ai@pve` user + least-privilege `PVEAIOperator` role (root@pam token kept only as a labeled quick-test fallback); cross-referenced from `docs/ct-setup.md`, `docs/troubleshooting.md`, `install.sh`, `install-remote.sh`. Not applied to the live cluster (would require switching the working `pve.api_token` — left as a manual, tested-by-you migration)

## Phase 8 — Code Review Findings (2026-09-25)
- [x] 8.1  Local-vs-deployed parity audit — diffed all 16 `backend/` files, `ai-panel.js`, `ai-settings.js`, `patch.sh`, `unpatch.sh`, the dpkg hook, and `proxmox-ai.service` against the live copies on CT 130 / the PVE host. Everything now matches byte-for-byte. Previously drifted (found + fixed 2026-09-25): `config.py` (missing `system_prompt` field), `sessions.py` (missing `delete_all`), `app.py` (missing `default_system_prompt` + `DELETE /audit` + `DELETE /sessions` routes, in BOTH local and production), `ai-panel.js` (missing the whole History feature, reintroduced close-button bug)
- [x] 8.2  `_stream_task_progress()` is called in `app.py` (chat auto-exec path + `GET /exec/{id}/task`) but never defined anywhere — `NameError` crash whenever an auto-executed command's output contains a UPID, or whenever the task-progress SSE endpoint is hit. `tasks.watch()` exists and is imported but never wired in. Blocks 3.4/3.8 and makes 5.4 non-functional in practice — FIXED 2026-09-25: defined in `app.py` using `tasks.watch()`; deployed to CT 130 and health-checked
- [x] 8.3  `_has_admin_caps()` checks `caps.get("vms", {})`/`caps.get("sys", {})`, but PVE's `/access/permissions` response is keyed by **path** (e.g. `"/"`), not by category — non-root `PVEAdmin` users are almost certainly never recognized as admin; only the hardcoded `root@pam` check currently grants access — FIXED 2026-09-25: now reads `caps["/"]`; deployed and unit-verified in production against both the correct PVE response shape and the old (now-rejected) wrong shape
- [ ] 8.4  No local git repository in `proxmox-ai/` — `github-push.sh` only manages a separate sanitized staging copy, so the working copy has no diff/history safety net (this is how the 8.1 drift went undetected). Recommend `git init` here (compatible with `github-push.sh`, which already excludes `.git` from its rsync) — DEFERRED: explicitly not to be done without the user's own direction
- [x] 8.5  7.10 follow-up — backend port 9000 was reachable directly from anywhere on the LAN, bypassing the TLS proxy entirely (confirmed: direct `curl` from an unrelated host got a 200). FIXED 2026-09-25 without touching the shared PVE cluster firewall (which was cluster-wide disabled — enabling it would have affected every other guest, so that path was avoided): added a CT-local nftables rule (`/etc/nftables.conf` in CT 130) restricting tcp/9000 to the PVE host IP only, applied via the CT's own already-enabled `nftables.service`. Verified: direct access now refused, the host's TLS-proxy path still returns 200

## Backlog (v1.1+)
- [ ] B.1  Native tool-calling for vLLM/Qwen3 (Hermes parser)
- [ ] B.2  TLS for backend service
- [ ] B.3  RAG over cluster logs/status history
- [ ] B.4  Per-PVE-user AI permission profiles (beyond admin-only)
- [ ] B.5  Mobile-friendly panel