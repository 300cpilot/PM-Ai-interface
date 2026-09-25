#!/bin/bash
# proxmox-ai one-shot installer.
# Run ON THE PVE HOST (as root). Creates the backend CT, installs the backend,
# generates SSH keys, applies the UI patch, and installs the dpkg re-patch hook.
#
# Default posture after install: everything OFF except local vLLM chat.
# The admin opts into power via the Settings tab.
set -euo pipefail

CTID="${CTID:-130}"
CT_IP="${CT_IP:-192.168.1.XXX/24}"
CT_GW="${CT_GW:-192.168.1.XXX}"
CT_HOSTNAME="proxmox-ai"
BACKEND_PORT=9000
TLS_PORT="${TLS_PORT:-9443}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- environment defaults (this project: node01 + vLLM in CT 100) ---
PVE_NODE_NAME="${PVE_NODE_NAME:-node01}"
PVE_NODE_IP="${PVE_NODE_IP:-192.168.1.XXX}"
VLLM_URL="${VLLM_URL:-http://192.168.1.XXX:8000/v1}"
VLLM_MODEL="${VLLM_MODEL:-qwen3-30b-a3b}"
# No default key — pass VLLM_KEY explicitly if your server requires one (7.8)
VLLM_KEY="${VLLM_KEY:-}"

echo "=== proxmox-ai installer ==="
echo "CT ID: $CTID  IP: $CT_IP  GW: $CT_GW"

# --- 0. pre-flight: IP must be free ---
CT_IP_ONLY="${CT_IP%/*}"
if ! pct status "$CTID" >/dev/null 2>&1; then
    echo "[0/7] checking $CT_IP_ONLY is not already in use"
    # check existing guest configs
    if grep -rqs "ip=${CT_IP_ONLY}/\|ip=${CT_IP_ONLY}\b" /etc/pve/qemu-server/ /etc/pve/lxc/ 2>/dev/null; then
        echo "ERROR: $CT_IP_ONLY is assigned to another guest in /etc/pve" >&2
        exit 1
    fi
    # check the wire
    if ping -c 2 -W 2 "$CT_IP_ONLY" >/dev/null 2>&1; then
        echo "ERROR: $CT_IP_ONLY responds to ping — already in use" >&2
        exit 1
    fi
    echo "[0/7] $CT_IP_ONLY is free"
fi

# --- 1. create CT (skip if exists) ---
if pct status "$CTID" >/dev/null 2>&1; then
    echo "[1/7] CT $CTID already exists, skipping creation"
else
    echo "[1/7] creating CT $CTID (Debian 13)"
    TEMPLATE=$(pveam available --section system | awk '/debian-13-standard/ {print $2}' | head -1)
    pveam download local "$TEMPLATE" || true
    pct create "$CTID" "local:vztmpl/$TEMPLATE" \
        --hostname "$CT_HOSTNAME" \
        --cores 2 --memory 2048 --rootfs local-lvm:8 \
        --net0 "name=eth0,bridge=vmbr0,ip=$CT_IP,gw=$CT_GW" \
        --unprivileged 1 --start 1
    sleep 5
fi

# --- 2. install backend in CT ---
echo "[2/7] installing backend in CT $CTID"
pct exec "$CTID" -- bash -c "
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip
    id proxmox-ai >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin proxmox-ai
    mkdir -p /opt/proxmox-ai /etc/proxmox-ai/keys /var/log/proxmox-ai /var/lib/proxmox-ai
    rm -f /opt/proxmox-ai/backend
"
# pct push can't copy directories — tar the backend in instead
tar czf - -C "$SRC_DIR" backend | pct exec "$CTID" -- tar xzf - -C /opt/proxmox-ai
pct push "$CTID" "$SRC_DIR/backend/proxmox-ai.service" /etc/systemd/system/proxmox-ai.service
pct exec "$CTID" -- bash -c "
    python3 -m venv /opt/proxmox-ai/venv
    /opt/proxmox-ai/venv/bin/pip install -q -r /opt/proxmox-ai/backend/requirements.txt
    chown -R proxmox-ai:proxmox-ai /opt/proxmox-ai /etc/proxmox-ai /var/log/proxmox-ai /var/lib/proxmox-ai
    chmod 700 /etc/proxmox-ai/keys
"

# --- 3. SSH keypair (CT -> this host) ---
echo "[3/7] generating SSH keypair and authorizing on host"
pct exec "$CTID" -- bash -c "
    [ -f /etc/proxmox-ai/keys/id_ed25519 ] || ssh-keygen -t ed25519 -N '' -f /etc/proxmox-ai/keys/id_ed25519 -q
    chown proxmox-ai:proxmox-ai /etc/proxmox-ai/keys/id_ed25519*
"
PUBKEY=$(pct exec "$CTID" -- cat /etc/proxmox-ai/keys/id_ed25519.pub)
# restrict: only from the CT's IP, no forwarding/pty/agent (7.7)
CT_IP_ONLY="${CT_IP%/*}"
AUTH_LINE="from=\"$CT_IP_ONLY\",restrict $PUBKEY"
grep -qF "$PUBKEY" /root/.ssh/authorized_keys 2>/dev/null || echo "$AUTH_LINE" >> /root/.ssh/authorized_keys

# --- 3b. pin the host's SSH host key in the CT (7.5) ---
echo "[3b/7] recording host SSH key in CT known_hosts"
pct exec "$CTID" -- bash -c "
    ssh-keyscan -T 10 -t ed25519,rsa,ecdsa $PVE_NODE_IP > /etc/proxmox-ai/keys/known_hosts 2>/dev/null
    chown proxmox-ai:proxmox-ai /etc/proxmox-ai/keys/known_hosts
    chmod 644 /etc/proxmox-ai/keys/known_hosts
"

# --- 4. seed config (locked down) ---
echo "[4/7] writing default config (everything OFF except local chat)"
HOST_IP=$(hostname -I | awk '{print $1}')
pct exec "$CTID" -- bash -c "cat > /etc/proxmox-ai/config.json" <<EOF
{
  "listen_host": "0.0.0.0",
  "listen_port": $BACKEND_PORT,
  "active_provider": "vllm-ct100",
  "providers": [
    {
      "id": "vllm-ct100",
      "type": "vllm",
      "name": "vLLM T4 Server (CT 100)",
      "base_url": "$VLLM_URL",
      "api_key": "$VLLM_KEY",
      "model": "$VLLM_MODEL",
      "enabled": true
    }
  ],
  "nodes": [
    {"name": "$PVE_NODE_NAME", "host": "$PVE_NODE_IP", "ssh_user": "root", "ssh_port": 22,
     "ssh_key": "/etc/proxmox-ai/keys/id_ed25519"}
  ],
  "pve": {
    "api_url": "https://$PVE_NODE_IP:8006/api2/json",
    "api_token": "",
    "verify_tls": true
  },
  "security": {
    "server_access": false,
    "internet_access": false,
    "online_providers": false,
    "guest_exec": false,
    "ssh_mode": "off",
    "categories": {
      "read_status": "off", "vmct_lifecycle": "off", "storage": "off",
      "network_firewall": "off", "users": "off", "host_system": "off",
      "guest_exec": "off"
    },
    "max_commands_per_minute": 10,
    "session_max_minutes": 120
  }
}
EOF
pct exec "$CTID" -- bash -c "
    chmod 600 /etc/proxmox-ai/config.json
    chown proxmox-ai:proxmox-ai /etc/proxmox-ai/config.json
    cat > /etc/logrotate.d/proxmox-ai <<'EOF'
/var/log/proxmox-ai/audit.log {
    weekly
    rotate 12
    compress
    missingok
    notifempty
    create 600 proxmox-ai proxmox-ai
}
EOF
    systemctl daemon-reload
    systemctl enable --now proxmox-ai
"

# --- 4b. TLS termination on the host (fixes HTTPS-page -> HTTP-backend mixed content) ---
# socat runs on the host and must reach the CT's IP — the backend binding to CT localhost would break this. Keep CT IP target; the CT-side firewall note goes to docs instead.
echo "[4b/7] setting up TLS proxy on port $TLS_PORT (PVE cert -> backend)"
cat > /etc/systemd/system/proxmox-ai-tls.service <<EOF
[Unit]
Description=TLS termination for proxmox-ai backend (mixed-content fix)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/socat OPENSSL-LISTEN:$TLS_PORT,reuseaddr,fork,cert=/etc/pve/local/pve-ssl.pem,key=/etc/pve/local/pve-ssl.key,verify=0 TCP:${CT_IP%/*}:$BACKEND_PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now proxmox-ai-tls

# --- 5. UI patch ---
echo "[5/7] patching PVE web UI"
mkdir -p /opt/proxmox-ai
rm -rf /opt/proxmox-ai/proxmox-ai
cp -a "$SRC_DIR/." /opt/proxmox-ai/
chmod +x /opt/proxmox-ai/frontend/patch.sh /opt/proxmox-ai/frontend/unpatch.sh
# persist the backend URL so the dpkg re-patch hook re-injects the right value
echo "https://$PVE_NODE_IP:$TLS_PORT" > /opt/proxmox-ai/backend-url
BACKEND_URL="https://$PVE_NODE_IP:$TLS_PORT" /opt/proxmox-ai/frontend/patch.sh

# --- 6. dpkg re-patch hook ---
echo "[6/7] installing dpkg re-patch hook"
cp -a "$SRC_DIR/frontend/99proxmox-ai-repatch" /etc/apt/apt.conf.d/99proxmox-ai-repatch

# --- 7. done ---
CT_IP_ONLY="${CT_IP%/*}"
echo "[7/7] verifying backend"
sleep 3
if pct exec "$CTID" -- curl -sf "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null; then
    echo ""
    echo "=== install complete ==="
    echo "Backend:  https://$PVE_NODE_IP:$TLS_PORT/health (TLS)  |  http://$CT_IP_ONLY:$BACKEND_PORT/health (direct)"
    echo "UI:       hard-refresh https://$PVE_NODE_IP:8006 and look for the AI button"
    echo ""
    echo "NEXT STEPS (in the AI Settings tab, root@pam only):"
    echo "  1. vLLM provider (CT 100, $VLLM_MODEL) is pre-configured and active — chat works now"
    echo "  2. PVE API token for live cluster context — recommended: a dedicated"
    echo "     proxmox-ai@pve user with a least-privilege role, see docs/installation.md"
    echo "     Step 4 (quick-test fallback: pveum user token add root@pam proxmox-ai --privsep 0)"
    echo "     then paste it into Settings (pve.api_token) to enable live cluster context"
    echo "  3. Enable toggles/categories as desired — all execution is OFF by default"
else
    echo "WARNING: backend health check failed. Inspect with:"
    echo "  pct exec $CTID -- journalctl -u proxmox-ai -n 50"
fi
