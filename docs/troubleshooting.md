# proxmox-ai — Troubleshooting Guide

Symptom-first guide. Every issue below was hit in production on this project.
Commands assume the defaults: PVE host `192.168.1.XXX`, backend CT `130`,
backend port `9000`, TLS proxy port `9443`.

Quick orientation — where to look:

| What | Command |
|------|---------|
| Backend logs | `pct exec 130 -- journalctl -u proxmox-ai -n 100 --no-pager` |
| Audit log (decisions, results) | `pct exec 130 -- tail -50 /var/log/proxmox-ai/audit.log` |
| Backend health | `curl -k https://192.168.1.XXX:9443/health` |
| Live config | `pct exec 130 -- cat /etc/proxmox-ai/config.json` |
| TLS proxy | `systemctl status proxmox-ai-tls` (on the host) |
| UI patch present | `grep -c proxmox-ai-inject /usr/share/pve-manager/js/pvemanagerlib.js` |
| Browser console | F12 → Console/Network on the PVE page |

---

## 1. UI / browser issues

### 1.1 The AI button is missing

Causes, in order of likelihood:

1. **Browser cache.** Hard-refresh with **Ctrl+Shift+R**. The button is
   injected by JS at the end of `pvemanagerlib.js`; a cached bundle won't have it.
2. **Patch not applied.** On the host:
   `grep proxmox-ai-inject /usr/share/pve-manager/js/pvemanagerlib.js`
   — if nothing, run `/opt/proxmox-ai/frontend/patch.sh`.
3. **pve-manager was upgraded and the dpkg hook failed.** Re-run
   `/opt/proxmox-ai/frontend/patch.sh` manually, then hard-refresh.
4. **JS error.** F12 → Console. If `ai-panel.js` or `ai-settings.js` 404s,
   pveproxy hasn't mapped `/pve2/js/ai/` — `patch.sh` restarts pveproxy for
   exactly this reason; re-run it.

### 1.2 Panel opens but everything fails: `NetworkError` / "CORS request did not succeed"

The browser doesn't trust the backend's TLS certificate (`:9443`, self-signed
PVE Cluster CA). Per-site "Accept the Risk" exceptions are **not reliably
honored for CORS preflights** (especially Firefox). Fix: import the PVE root CA
into the browser — see `browser-tls.md`. Verify with:

```js
// browser console on the PVE page
fetch(PVE_AI_BACKEND + '/health').then(r => r.json()).then(console.log)
// {ok: true, kill_switch: false}
```

Remember: cert stores are per-browser — fixing Chromium does nothing for
Firefox.

### 1.3 Settings → Permissions tab: can't edit / edits don't save

**Observed in production:** saves returned `200 OK` and Security-tab values
persisted, but category changes never reached the backend.

Root cause: the browser was running a **stale cached copy of
`ai-settings.js`**. pveproxy serves the AI assets with only a `Last-Modified`
header (no `Cache-Control`, no `ETag`), so browsers heuristically cache them
across deploys.

Fix (permanent, deployed 2026-09-19): `patch.sh` now stamps a
`?v=<timestamp>` cache-buster onto the asset URLs, updated on every re-patch.
If you're on an older patch: re-run the current `frontend/patch.sh` (or
`unpatch.sh && patch.sh`), then **hard-refresh every open PVE tab**.

Usage notes that look like bugs but aren't:

- Only the **Mode** column is editable — click directly on the
  `off`/`confirm`/`allow` text, not the category name.
- It's a single-click cell editor; the dropdown opens on the first click.
- You must click **Save** — edits are local until then.

Verify what the backend actually has (bypasses the UI entirely):

```bash
TICKET=$(curl -sk -X POST 'https://192.168.1.XXX:8006/api2/json/access/ticket' \
  --data-urlencode 'username=root@pam' --data-urlencode 'password=...' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["ticket"])')
curl -sk -H "PVE-Auth-Cookie: $TICKET" https://192.168.1.XXX:9443/config \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["security"])'
```

### 1.4 Settings → Save fails with "root@pam required"

Settings writes (`PUT /config`) require `root@pam` specifically — not just any
admin. Log in as root@pam. Denials are logged: check the audit log for
`auth_denied` events.

### 1.5 Settings loads but Providers grid is empty

Usually symptom 1.2 (TLS/CORS) — `GET /config` never completed. If TLS is fine,
check the browser Network tab for the `/config` response code:

- `401` — the panel isn't forwarding a valid `PVEAuthCookie`; log out/in.
- `403` — your user lacks admin capabilities (see 3.2).

---

## 2. Chat / LLM issues

### 2.1 "no active provider configured"

No provider is both `enabled` and selected as `active_provider`. Settings →
Providers → select a row → **Set Active** → Save. Or check
`active_provider` in `/etc/proxmox-ai/config.json`.

### 2.2 Provider test fails / chat errors

Settings → Providers → **Test**. Common causes:

- **vLLM down or wrong URL/model**: `curl http://192.168.1.XXX:8000/v1/models`
  from the CT: `pct exec 130 -- curl -s http://192.168.1.XXX:8000/v1/models`.
  The `model` field must exactly match what vLLM serves.
- **Blocked by toggles**: `online_providers` off blocks OpenAI/Anthropic/
  OpenRouter entirely; `internet_access` off blocks any non-RFC1918 base URL.
  The error message names the toggle.
- **Wrong API key**: keys are write-only (masked as `********` in GETs). To
  change one, edit the provider and type the new key; saving with the mask
  preserves the stored key.

### 2.3 The LLM proposes invalid commands (e.g. `pct create ... --disk 20`)

**Observed in production:** the model invented a `--disk` flag (real syntax:
`--rootfs <storage>:<GB>`) and hallucinated template names.

Fixed 2026-09-19: the system prompt now includes a `pct`/`qm` syntax reference
and the **live list of CT templates** from the node's storage. If you see
invented flags or template names:

1. Confirm the backend is running the updated `context.py`/`pve_api.py`:
   `pct exec 130 -- grep -c "syntax reference" /opt/proxmox-ai/backend/context.py`
2. Confirm the snapshot includes templates — the system prompt should end with
   `CT templates: local:vztmpl/...`. If it says `(PVE API token not configured)`,
   set up the API token (installation guide, step 4) — without it the model
   flies blind.
3. If a template you want is missing from the list, download it:
   `pveam download local <template>`.

### 2.4 Chat works but answers don't know about the cluster

The live snapshot requires the PVE API token (step 4 of the installation
guide). Without it the model only has generic knowledge. Also check the audit
log — snapshot fetch failures appear as `(cluster query failed: ...)` in the
prompt, usually a bad/expired token.

---

## 3. Command execution issues

### 3.1 Command "blocked" — how to read the reason

Every proposal is logged with its decision. Check the audit log:

```bash
pct exec 130 -- tail -5 /var/log/proxmox-ai/audit.log | python3 -c '
import sys, json
for line in sys.stdin:
    e = json.loads(line)
    if e.get("event") == "exec_request":
        print(e["decision"], "|", e["reason"], "|", e["command"][:80])'
```

| Reason | Fix |
|--------|-----|
| `kill switch active` | `pct exec 130 -- rm /etc/proxmox-ai/DISABLED` |
| `server_access is off` | Settings → Security → enable master switch |
| `category 'X' is off` | Settings → Permissions → set category to `confirm` or `allow` |
| `category 'X' requires approval` | Working as intended — click **Approve** in chat |
| `ssh_mode is off` | Settings → Security → set SSH mode |
| `not on allowlist` | `ssh_mode=allowlist` only auto-runs read-only commands; approve manually, change mode, or extend the allowlist (see below) |
| `denylist: destructive command...` | Always requires approval, by design |
| `rate limit exceeded` | Wait a minute, or raise `max_commands_per_minute` |
| `guest_exec toggle is off` | Settings → Security → enable guest exec |

### 3.1.1 Modifying the SSH allowlist

The allowlist is **not** exposed in the UI or `config.json` — it is hardcoded
as `ALLOWLIST_PATTERNS` (a list of regex patterns) in
`backend/permissions.py`. To add or remove auto-run commands:

```bash
pct exec 130 -- $EDITOR /opt/proxmox-ai/backend/permissions.py
pct exec 130 -- systemctl restart proxmox-ai
```

Notes:

- Patterns are matched with `re.search` against the stripped command — anchor
  with `^` to match from the start (e.g. `r"^pvesm\s+status\b"`).
- Commands not matching the allowlist fall back to requiring confirmation.
- Commands matching `DENYLIST_PATTERNS` in the same file always require
  approval, even if also allowlisted.

See [permissions.md](permissions.md) for the full category reference, the
decision pipeline, and how to add allowlist/denylist patterns or new
categories.

### 3.2 Approved command fails immediately with SSH errors

The backend SSHes from the CT to the node as root using
`/etc/proxmox-ai/keys/id_ed25519`. Test it directly:

```bash
pct exec 130 -- ssh -i /etc/proxmox-ai/keys/id_ed25519 \
  -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  root@192.168.1.XXX 'hostname'
```

- **Permission denied**: the pubkey isn't in the host's
  `/root/.ssh/authorized_keys` (or was removed). Re-add:
  `pct exec 130 -- cat /etc/proxmox-ai/keys/id_ed25519.pub` → append to the host.
- **Connection timeout/refused**: wrong `host`/`ssh_port` in the `nodes` list,
  or sshd not reachable from the CT (firewall).
- **Works manually but not from the service**: the key must be readable by the
  `proxmox-ai` user — `pct exec 130 -- ls -l /etc/proxmox-ai/keys/`.

### 3.3 Approved command runs but fails (non-zero exit)

The full stderr is in the chat bubble and the audit log (`exec_result` events,
output truncated to 4000 chars). Most common: LLM-generated syntax errors —
see 2.3. Run the command manually on the node to iterate quickly, then tell the
model what was wrong.

### 3.4 Approve button does nothing / "request is not pending"

Exec requests live in memory in the backend. If the backend was restarted
between proposal and approval, the request is gone (`404 unknown request id`)
or already acted on (`409 ... not pending`). Just ask the model again.

---

## 4. Backend service issues

### 4.1 Backend won't start

```bash
pct exec 130 -- journalctl -u proxmox-ai -n 100 --no-pager
```

- **`ValidationError` on config**: `/etc/proxmox-ai/config.json` is malformed
  (hand-edit gone wrong). Fix or delete it — the service starts with defaults
  (everything off) if the file is missing.
- **Address in use**: something else on `:9000` in the CT.
- **Import errors**: venv broken — reinstall:
  `pct exec 130 -- /opt/proxmox-ai/venv/bin/pip install -r /opt/proxmox-ai/backend/requirements.txt`

### 4.2 `:9443` dead but backend healthy

The TLS proxy is down: `systemctl status proxmox-ai-tls` on the host. Common
cause: `pve-ssl.pem` regenerated (cert renewal) — the service restarts on
failure, but check `journalctl -u proxmox-ai-tls`. Also confirm the CT IP
hasn't changed; the proxy targets `TCP:<CT_IP>:9000`.

### 4.3 `GET /config` returns 401 in the browser but curl works

The panel forwards `Ext.util.Cookies.get('PVEAuthCookie')`. If you logged in
with "remember me" off, or the ticket expired, the cookie may be stale — log
out and back in. The backend caches ticket validation for 60s, so a just-
expired ticket can briefly keep working.

---

## 5. Maintenance recipes

### Deploying code changes

Backend (example: `context.py`):

```bash
scp backend/context.py root@192.168.1.XXX:/tmp/
ssh root@192.168.1.XXX 'pct push 130 /tmp/context.py /opt/proxmox-ai/backend/context.py \
  && pct exec 130 -- chown proxmox-ai:proxmox-ai /opt/proxmox-ai/backend/context.py \
  && pct exec 130 -- systemctl restart proxmox-ai'
```

Frontend (any `frontend/*.js` change):

```bash
scp frontend/ai-settings.js root@192.168.1.XXX:/opt/proxmox-ai/frontend/
ssh root@192.168.1.XXX '/opt/proxmox-ai/frontend/patch.sh'   # copies assets, bumps cache-buster, restarts pveproxy
# then hard-refresh all browsers
```

Config changes take effect immediately on save (no restart needed) — the
backend reloads from the in-memory object updated by `PUT /config`. Hand-edits
to `config.json` require `pct exec 130 -- systemctl restart proxmox-ai`.

### Rotating the PVE API token

```bash
pveum user token remove root@pam proxmox-ai
pveum user token add root@pam proxmox-ai --privsep 0
# update pve.api_token in /etc/proxmox-ai/config.json, restart backend
```

### Adding another cluster node

1. Append to `nodes` in `/etc/proxmox-ai/config.json`:
   `{"name": "node02", "host": "192.168.1.x", "ssh_user": "root", "ssh_port": 22, "ssh_key": "/etc/proxmox-ai/keys/id_ed25519"}`
2. Authorize the CT's pubkey on that node:
   `pct exec 130 -- cat /etc/proxmox-ai/keys/id_ed25519.pub` → append to the
   node's `/root/.ssh/authorized_keys`.
3. `pct exec 130 -- systemctl restart proxmox-ai`.

### Emergency stop

```bash
pct exec 130 -- touch /etc/proxmox-ai/DISABLED   # everything 503s instantly
pct exec 130 -- rm /etc/proxmox-ai/DISABLED      # re-enable
```

Or harder: `pct exec 130 -- systemctl stop proxmox-ai` (UI shows connection
errors; the rest of PVE is unaffected).

---

## 6. Known limitations (v0.1)

- **Settings UI doesn't expose `pve.api_token` or `nodes`** — edit
  `config.json` directly (see installation guide step 4).
- **Kill switch is file-based** — the UI button only shows instructions.
- **Exec requests are in-memory** — backend restart drops pending approvals.
- **No streaming of command output** — results appear when the command
  finishes (120s SSH timeout).
- **Audit log is append-only JSONL** — no rotation configured; add logrotate
  if it grows.
