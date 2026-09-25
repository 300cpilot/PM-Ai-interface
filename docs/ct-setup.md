# proxmox-ai Backend CT Setup

The backend runs in a dedicated, unprivileged LXC container on the PVE host.
It brokers all LLM traffic, enforces the security model, and executes approved
commands via SSH / the PVE REST API.

## Resources

| Property | Value |
|----------|-------|
| CT ID    | 130 (suggested) |
| OS       | Debian 13 (trixie) standard template |
| Cores    | 2 |
| RAM      | 2048 MB |
| Disk     | 8 GB (local-lvm) |
| Network  | vmbr0, static IP (e.g. 192.168.1.XXX/24) |
| User     | `proxmox-ai` (system user, nologin) |

## Layout inside the CT

| Path | Purpose |
|------|---------|
| `/opt/proxmox-ai/backend` | FastAPI application |
| `/opt/proxmox-ai/venv` | Python virtualenv |
| `/etc/proxmox-ai/config.json` | Config (chmod 600, contains API keys) |
| `/etc/proxmox-ai/keys/id_ed25519` | SSH key to PVE host(s) |
| `/etc/proxmox-ai/DISABLED` | Kill switch — create this file to halt everything |
| `/var/log/proxmox-ai/audit.log` | Append-only audit log (JSONL) |
| `/var/lib/proxmox-ai/sessions.db` | Chat session store (SQLite) |
| systemd unit | `proxmox-ai.service` (port 9000) |

## TLS proxy on the PVE host (port 9443)

The UI page is HTTPS (`:8006`) but the backend in the CT is plain HTTP
(`:9000`) — browsers refuse that as mixed content. `install.sh` therefore
installs `proxmox-ai-tls.service` **on the PVE host**: a socat
`OPENSSL-LISTEN` on `:9443` using the host's own PVE cert
(`/etc/pve/local/pve-ssl.pem`), forwarding to the CT's `:9000`.

Consequence: the panel's requests are cross-origin against a self-signed-cert
endpoint, so each browser must trust the PVE Cluster CA — see
`docs/browser-tls.md`.

## Manual creation (if not using install.sh)

```bash
pveam download local $(pveam available --section system | awk '/debian-13-standard/ {print $2}' | head -1)
pct create 130 local:vztmpl/<template> \
    --hostname proxmox-ai --cores 2 --memory 2048 \
    --rootfs local-lvm:8 \
    --net0 name=eth0,bridge=vmbr0,ip=192.168.1.XXX/24,gw=192.168.1.XXX \
    --unprivileged 1 --start 1
```

## PVE API token (backend → PVE)

Recommended: a dedicated `proxmox-ai@pve` user with a least-privilege role —
see `docs/installation.md` Step 4 for the full role/ACL setup. Quick-test
fallback (broader access, avoid outside a throwaway cluster):

```bash
pveum user token add root@pam proxmox-ai --privsep 0
# paste the printed token value into Settings → pve.api_token
```

Revoke with: `pveum user token remove root@pam proxmox-ai` (or
`pveum user token remove proxmox-ai@pve backend` for the dedicated user).

## Adding cluster nodes

1. Append the node to `nodes` in `/etc/proxmox-ai/config.json`.
2. Install the CT's pubkey on that node:
   `ssh root@<node> 'echo "<pubkey>" >> /root/.ssh/authorized_keys'`
3. `systemctl restart proxmox-ai` in the CT.
