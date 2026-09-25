# proxmox-ai — Installation Guide

Complete installation from a bare PVE host to a working AI assistant in the web
UI. For architecture and the security model see `../README.md`; for container
details see `ct-setup.md`; for browser certificate setup see `browser-tls.md`.
If anything goes wrong, see `troubleshooting.md`.

## Overview

The installer (`install.sh`) does everything, on the PVE host, as root:

1. Pre-flight: verifies the CT IP is free (guest configs + ping).
2. Creates an unprivileged Debian 13 LXC (default CT 130, 2 cores / 2 GB / 8 GB).
3. Installs the FastAPI backend into the CT (`/opt/proxmox-ai/backend`, venv,
   systemd unit, `proxmox-ai` system user).
4. Generates an SSH keypair in the CT and authorizes the pubkey on the host
   (`/root/.ssh/authorized_keys`) — this is how approved commands execute.
5. Seeds `/etc/proxmox-ai/config.json` — **everything OFF except chat** with the
   pre-configured local vLLM provider.
6. Installs a socat TLS proxy **on the host** (`:9443` → CT `:9000`) using the
   PVE host's own certificate — fixes HTTPS-page → HTTP-backend mixed content.
7. Patches the PVE web UI (`frontend/patch.sh`) and installs a dpkg hook that
   re-applies the patch after every `pve-manager` upgrade.

## Prerequisites

- PVE 8/9 host, root shell access.
- A free static IP for the backend CT on your LAN bridge (`vmbr0`).
- A local LLM endpoint if you want chat out of the box (this project uses vLLM
  in CT 100 at `http://192.168.1.XXX:8000/v1`). Online providers can be added
  later via Settings.
- `socat` on the host (installed by default on PVE; `apt install socat` if not).

## Step 1 — Configure

All installer knobs are environment variables with defaults matching this
project's environment. Override what differs:

| Variable | Default | Meaning |
|----------|---------|---------|
| `CTID` | `130` | Container ID for the backend |
| `CT_IP` | `192.168.1.XXX/24` | Backend CT static IP |
| `CT_GW` | `192.168.1.XXX` | Gateway |
| `TLS_PORT` | `9443` | Host-side TLS proxy port |
| `PVE_NODE_NAME` | `node01` | Node name (as shown in the UI) |
| `PVE_NODE_IP` | `192.168.1.XXX` | Node IP (PVE API + SSH target) |
| `VLLM_URL` | `http://192.168.1.XXX:8000/v1` | Local vLLM endpoint |
| `VLLM_MODEL` | `qwen3-30b-a3b` | Model name served by vLLM |
| `VLLM_KEY` | (project key) | vLLM API key |

## Step 2 — Run

```bash
cd proxmox-ai
./install.sh
# or with overrides:
CTID=140 CT_IP=192.168.1.XXX/24 VLLM_MODEL=my-model ./install.sh
```

Expected ending:

```
=== install complete ===
Backend:  https://192.168.1.XXX:9443/health (TLS)  |  http://192.168.1.XXX:9000/health (direct)
UI:       hard-refresh https://192.168.1.XXX:8006 and look for the AI button
```

The installer is idempotent: re-running skips CT creation, refreshes the
backend code, and re-applies the UI patch.

## Step 3 — Browser TLS trust (one time per browser)

The AI panel talks to `https://<host>:9443`, a different origin than the UI
(`:8006`), using the self-signed PVE Cluster CA. Until the browser trusts that
CA, every panel request fails with `NetworkError` / "CORS request did not
succeed" and Settings → Providers stays empty.

Follow `browser-tls.md` — import `/etc/pve/pve-root-ca.pem` into each browser
(Firefox and Chromium have separate stores). Verify:

```bash
curl https://<pve-host>:9443/health     # no -k needed after CA import
# {"ok":true,"kill_switch":false}
```

## Step 4 — PVE API token (enables live cluster context)

The system prompt includes a live cluster snapshot (nodes, guests, storage,
CT templates). The prompt itself can be viewed and overridden in
AI → Settings → System Prompt (empty = built-in default; `{snapshot}` marks
where the snapshot is injected). The snapshot needs an API token.

**Recommended: dedicated `proxmox-ai@pve` user with a least-privilege role**
(not a `root@pam` token). A root token can do anything on the cluster; a
scoped role limits the blast radius if the token ever leaks, and is revocable
without touching the `root@pam` account.

```bash
# 1. Create a role covering what the backend actually calls (node/guest/task
#    read, guest lifecycle + agent exec, storage read/allocate). Sys.Modify +
#    VM.Allocate at "/" are also what auth.py checks to recognize this as an
#    admin-equivalent identity — do not drop those two.
pveum role add PVEAIOperator -privs "Sys.Audit,Sys.Modify,VM.Audit,VM.Monitor,\
VM.PowerMgmt,VM.Allocate,VM.Clone,VM.Migrate,VM.Snapshot,VM.Config.Disk,\
VM.Config.CPU,VM.Config.Memory,VM.Config.Network,VM.Config.Options,\
Datastore.Audit,Datastore.AllocateSpace"

# 2. Create the user and grant the role at the root path (cluster-wide)
pveum user add proxmox-ai@pve --comment "proxmox-ai backend"
pveum acl modify / -user proxmox-ai@pve -role PVEAIOperator

# 3. Issue a token from that user (not root@pam)
pveum user token add proxmox-ai@pve backend --privsep 0
# copy the printed token VALUE (shown once)
```

> This role list is a starting point, not a guarantee — if a specific
> operation 403s, PVE's task log names the missing privilege; add it with
> `pveum role modify PVEAIOperator -privs <existing-list>,<MissingPrivilege>`.

**Quick-test fallback** (root token, faster but broader access — avoid outside
a throwaway test cluster):

```bash
pveum user token add root@pam proxmox-ai --privsep 0
```

In the UI: AI → Settings → edit the config's `pve.api_token` — currently this
is done by editing `/etc/proxmox-ai/config.json` in the CT (the Settings UI
doesn't expose it yet):

```bash
pct exec 130 -- bash -c '
  python3 - <<EOF
import json
p = "/etc/proxmox-ai/config.json"
c = json.load(open(p))
c["pve"]["api_token"] = "root@pam!proxmox-ai=<TOKEN-VALUE>"
open(p, "w").write(json.dumps(c, indent=2))
EOF
  chmod 600 /etc/proxmox-ai/config.json
  systemctl restart proxmox-ai'
```

## Step 5 — Enable capabilities (Settings tab)

Open the PVE UI, hard-refresh (**Ctrl+Shift+R**), click **AI** → **Settings**.
Settings writes require `root@pam`.

Recommended starting posture:

| Setting | Value | Why |
|---------|-------|-----|
| `server_access` | on | Master switch — nothing executes without it |
| `ssh_mode` | `confirm` | Every command needs your approval |
| `read_status` | `allow` | Read-only commands (`qm list`, `df`, …) auto-run |
| `vmct_lifecycle` | `confirm` | VM/CT create/start/stop need approval |
| everything else | `off` | Opt in later |

Notes:

- **Full reference**: see [permissions.md](permissions.md) for every category,
  what commands it covers, how the modes interact with `ssh_mode`, and how to
  extend the allowlist/denylist/categories.
- **Permissions tab**: only the **Mode** column is editable — click directly on
  the `off`/`confirm`/`allow` value (single click opens the dropdown), pick the
  new mode, then click **Save**. Clicking the category name does nothing.
- The **denylist** (rm -rf /, mkfs, zpool destroy, shutdown, …) always requires
  approval, even in `auto` mode.
- The **allowlist** (commands that auto-run when `ssh_mode=allowlist`) is not
  in the config file or UI — it is hardcoded as `ALLOWLIST_PATTERNS` in
  `backend/permissions.py`. To change it, edit that list of regex patterns on
  the CT (`/opt/proxmox-ai/backend/permissions.py`) and restart the backend:
  `pct exec 130 -- systemctl restart proxmox-ai`. Patterns are matched with
  `re.search` against the stripped command; anchor with `^` to match from the
  start. Anything not matching falls back to requiring confirmation.
- Kill switch: `pct exec 130 -- touch /etc/proxmox-ai/DISABLED` instantly 503s
  all chat/exec — including **pending approvals** (re-checked at execution
  time). Remove the file to re-enable.
- **TLS verification** for the PVE API defaults to `verify_tls: true`. With the
  default self-signed PVE cert, either install the PVE CA on the CT or set
  `verify_tls: false` in Settings (understand the MITM risk first).
- The CT's SSH key is added to the host's `authorized_keys` with
  `from="<CT_IP>",restrict` — it only works from the CT and cannot forward
  ports/agents. The host's SSH host key is pinned in the CT's
  `/etc/proxmox-ai/keys/known_hosts` at install time; if you rebuild or rekey
  the host, re-run `ssh-keyscan` or SSH exec will refuse to connect.
- `VLLM_KEY` has no default — export it before running `install.sh` if your
  vLLM server requires a key.
- **Uninstall**: `./uninstall.sh` on the host removes the UI patch, dpkg hook,
  TLS proxy, and authorized_keys entry; `DESTROY_CT=1 ./uninstall.sh` also
  destroys the backend CT. Revoke the API token manually:
  `pveum user token remove root@pam proxmox-ai`.

## Step 6 — Verify

1. **Chat**: ask "what's the cluster status?" — the answer should reflect the
   live snapshot (proves provider + API token work).
2. **Read-only exec**: ask "list my containers" — with `read_status: allow` and
   `ssh_mode: confirm`, `pct list` runs after one approval (or automatically in
   `allowlist` mode).
3. **Lifecycle exec**: ask "create a CT named test-001, 1 core, 2 GB RAM, 20 GB
   disk" — expect a proposal like:

   ```
   pct create 131 local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst \
     --hostname test-001 --memory 2048 --cores 1 --rootfs local-lvm:20 \
     --net0 name=eth0,bridge=vmbr0,ip=dhcp --unprivileged 1
   ```

   Click **Approve** in the chat; the result (exit code + output) appears inline.
4. **Audit**: Settings → Audit Log shows every chat, config change, and command
   with its decision and result.

## File layout reference

| Location | Host / CT | Purpose |
|----------|-----------|---------|
| `/opt/proxmox-ai/` | host | Copy of this repo (patch.sh source for the dpkg hook) |
| `/opt/proxmox-ai/backend-url` | host | Persisted backend URL for re-patches |
| `/usr/share/pve-manager/js/ai/` | host | Injected UI assets (`ai-panel.js`, `ai-settings.js`) |
| `/usr/share/pve-manager/js/pvemanagerlib.js` | host | Patched UI bundle (backup: `.proxmox-ai.bak`) |
| `/etc/apt/apt.conf.d/99proxmox-ai-repatch` | host | dpkg re-patch hook |
| `proxmox-ai-tls.service` | host | socat TLS proxy `:9443` → CT `:9000` |
| `/opt/proxmox-ai/backend/` | CT | FastAPI app + venv |
| `/etc/proxmox-ai/config.json` | CT | Config (chmod 600 — contains API keys) |
| `/etc/proxmox-ai/keys/id_ed25519` | CT | SSH key to PVE host(s) |
| `/etc/proxmox-ai/DISABLED` | CT | Kill switch (create to halt everything) |
| `/var/log/proxmox-ai/audit.log` | CT | Append-only JSONL audit log |
| `/var/lib/proxmox-ai/sessions.db` | CT | Chat session store (SQLite) |

## Upgrades

- **Backend/frontend changes**: copy the changed files over (see
  `troubleshooting.md` → "Deploying code changes"), restart `proxmox-ai` in the
  CT, re-run `frontend/patch.sh` on the host if UI files changed, hard-refresh
  the browser.
- **pve-manager upgrades**: the dpkg hook re-applies the UI patch automatically
  and restarts pveproxy. Hard-refresh the browser afterwards.

## Installing on additional servers (shared vLLM)

To put the AI panel on a **different PVE host** while reusing the same vLLM
server, use `install-remote.sh` instead of `install.sh`. It prompts for the
target's node name/IP/CT network (or takes them as env vars), keeps the vLLM
defaults pointed at the shared server, and verifies the new backend CT can
actually reach vLLM before finishing:

```bash
scp -r proxmox-ai root@<new-host>:/root/
ssh root@<new-host> 'cd /root/proxmox-ai && ./install-remote.sh'

# non-interactive:
ssh root@<new-host> 'cd /root/proxmox-ai && \
  PVE_NODE_NAME=node02 PVE_NODE_IP=192.168.1.XXX \
  CT_IP=192.168.1.XXX/24 CT_GW=192.168.1.XXX \
  ./install-remote.sh'
```

Per-host notes:

- **Each host gets its own backend CT** — config, audit log, sessions, and
  permissions are independent per server.
- **Each cluster has its own self-signed CA** — browsers must import the new
  host's `/etc/pve/pve-root-ca.pem` too; trusting the first server's CA does
  not cover the new one.
- **vLLM must be reachable across subnets** — vLLM must bind `0.0.0.0` (not
  `127.0.0.1`) and any firewall on the vLLM host must allow port 8000 from the
  new CT. The installer checks this and warns if it fails.
- The new host needs its own **PVE API token** (`pveum user token add
  root@pam proxmox-ai --privsep 0` on that host) for live cluster context.

## Uninstall

```bash
/opt/proxmox-ai/frontend/unpatch.sh          # restore original pvemanagerlib.js
rm /etc/apt/apt.conf.d/99proxmox-ai-repatch  # remove dpkg hook
systemctl disable --now proxmox-ai-tls       # remove TLS proxy
rm /etc/systemd/system/proxmox-ai-tls.service && systemctl daemon-reload
pct stop 130 && pct destroy 130              # remove backend CT
pveum user token remove root@pam proxmox-ai  # revoke API token (if created)
# remove the proxmox-ai pubkey line from /root/.ssh/authorized_keys
```
