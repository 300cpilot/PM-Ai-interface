#!/bin/bash
# proxmox-ai REMOTE installer — for additional PVE servers that share the
# existing vLLM server (CT 100 on the original host).
#
# Differences from install.sh:
#   - Prompts for (or takes env vars for) the target node's name/IP/network —
#     no hardcoded node01/192.168.1.XXX defaults.
#   - vLLM defaults stay pointed at the shared server (192.168.1.XXX) — the new
#     backend CT just needs network reachability to it.
#   - Verifies vLLM reachability from the new CT before finishing.
#
# Run ON THE TARGET PVE HOST (as root), from a copy of this repo:
#   scp -r proxmox-ai root@<new-host>:/root/
#   ssh root@<new-host> 'cd /root/proxmox-ai && ./install-remote.sh'
#
# Non-interactive example:
#   PVE_NODE_NAME=node02 PVE_NODE_IP=192.168.1.XXX \
#   CT_IP=192.168.1.XXX/24 CT_GW=192.168.1.XXX \
#   ./install-remote.sh
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT=9000
CT_HOSTNAME="proxmox-ai"

# --- shared vLLM server (same for all hosts) ---
VLLM_URL="${VLLM_URL:-http://192.168.1.XXX:8000/v1}"
VLLM_MODEL="${VLLM_MODEL:-qwen3-30b-a3b}"
# No default key — pass VLLM_KEY explicitly if your server requires one (7.8)
VLLM_KEY="${VLLM_KEY:-}"

# --- helpers ---------------------------------------------------------------

prompt() {  # prompt <varname> <prompt-text> [default]
    local var="$1" text="$2" default="${3:-}"
    # env var already set wins (non-interactive use)
    if [[ -n "${!var:-}" ]]; then return; fi
    if [[ -n "$default" ]]; then
        read -rp "$text [$default]: " "$var" </dev/tty
        printf -v "$var" '%s' "${!var:-$default}"
    else
        while [[ -z "${!var:-}" ]]; do
            read -rp "$text: " "$var" </dev/tty
        done
    fi
    export "$var"
}

die() { echo "ERROR: $*" >&2; exit 1; }

# --- sanity: must run on a PVE host ----------------------------------------

command -v pct >/dev/null || die "pct not found — run this on a Proxmox VE host as root"
[[ $EUID -eq 0 ]] || die "run as root"

echo "=== proxmox-ai remote installer (shared vLLM: $VLLM_URL) ==="
echo

# --- gather target-specific settings ---------------------------------------

# node name: default to this host's PVE node name
DEFAULT_NODE="$(hostname -s)"
prompt PVE_NODE_NAME "PVE node name (as shown in the UI)" "$DEFAULT_NODE"

# node IP: default to the primary IP of this host
DEFAULT_IP="$(hostname -I | awk '{print $1}')"
prompt PVE_NODE_IP "This host's IP (PVE API + SSH target)" "$DEFAULT_IP"

prompt CTID "Container ID for the backend CT" "130"

# CT network: guess the /24 of the node IP as a starting point
IP_PREFIX="${PVE_NODE_IP%.*}"
prompt CT_IP "Backend CT static IP (CIDR)" "${IP_PREFIX}.130/24"
prompt CT_GW "Gateway for the backend CT" "${IP_PREFIX}.1"
prompt TLS_PORT "Host-side TLS proxy port" "9443"

CT_IP_ONLY="${CT_IP%/*}"

echo
echo "--- plan ---"
echo "  node:        $PVE_NODE_NAME ($PVE_NODE_IP)"
echo "  backend CT:  $CTID at $CT_IP (gw $CT_GW)"
echo "  TLS proxy:   https://$PVE_NODE_IP:$TLS_PORT -> $CT_IP_ONLY:$BACKEND_PORT"
echo "  vLLM:        $VLLM_URL (model $VLLM_MODEL)"
echo
read -rp "Proceed? [y/N] " CONFIRM </dev/tty
[[ "${CONFIRM,,}" == "y" ]] || die "aborted"

# --- 0. pre-flight: CT IP must be free --------------------------------------

if ! pct status "$CTID" >/dev/null 2>&1; then
    echo "[0/8] checking $CT_IP_ONLY is not already in use"
    if grep -rqs "ip=${CT_IP_ONLY}/\|ip=${CT_IP_ONLY}\b" /etc/pve/qemu-server/ /etc/pve/lxc/ 2>/dev/null; then
        die "$CT_IP_ONLY is assigned to another guest in /etc/pve"
    fi
    if ping -c 2 -W 2 "$CT_IP_ONLY" >/dev/null 2>&1; then
        die "$CT_IP_ONLY responds to ping — already in use"
    fi
    echo "[0/8] $CT_IP_ONLY is free"
fi

# --- 1. create CT (skip if exists) ------------------------------------------

if pct status "$CTID" >/dev/null 2>&1; then
    echo "[1/8] CT $CTID already exists, skipping creation"
else
    echo "[1/8] creating CT $CTID (Debian 13)"
    TEMPLATE=$(pveam available --section system | awk '/debian-13-standard/ {print $2}' | head -1)
    [[ -n "$TEMPLATE" ]] || die "no debian-13-standard template found by pveam"
    pveam download local "$TEMPLATE" || true
    pct create "$CTID" "local:vztmpl/$TEMPLATE" \
        --hostname "$CT_HOSTNAME" \
        --cores 2 --memory 2048 --rootfs local-lvm:8 \
        --net0 "name=eth0,bridge=vmbr0,ip=$CT_IP,gw=$CT_GW" \
        --unprivileged 1 --start 1
    sleep 5
fi

# --- 2. install backend in CT ------------------------------------------------

echo "[2/8] installing backend in CT $CTID"
pct exec "$CTID" -- bash -c "
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip curl
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

# --- 3. SSH keypair (CT -> this host) ----------------------------------------

echo "[3/8] generating SSH keypair and authorizing on host"
pct exec "$CTID" -- bash -c "
    [ -f /etc/proxmox-ai/keys/id_ed25519 ] || ssh-keygen -t ed25519 -N '' -f /etc/proxmox-ai/keys/id_ed25519 -q
    chown proxmox-ai:proxmox-ai /etc/proxmox-ai/keys/id_ed25519*
"
PUBKEY=$(pct exec "$CTID" -- cat /etc/proxmox-ai/keys/id_ed25519.pub)
# restrict: only from the CT's IP, no forwarding/pty/agent (7.7)
CT_IP_ONLY="${CT_IP%/*}"
AUTH_LINE="from=\"$CT_IP_ONLY\",restrict $PUBKEY"
grep -qF "$PUBKEY" /root/.ssh/authorized_keys 2>/dev/null || echo "$AUTH_LINE" >> /root/.ssh/authorized_keys

# pin the host's SSH host key in the CT (7.5)
pct exec "$CTID" -- bash -c "
    ssh-keyscan -T 10 -t ed25519,rsa,ecdsa $PVE_NODE_IP > /etc/proxmox-ai/keys/known_hosts 2>/dev/null
    chown proxmox-ai:proxmox-ai /etc/proxmox-ai/keys/known_hosts
    chmod 644 /etc/proxmox-ai/keys/known_hosts
"

# --- 4. verify the shared vLLM server is reachable from the CT ---------------

echo "[4/8] checking vLLM reachability from CT $CTID -> $VLLM_URL"
if ! pct exec "$CTID" -- curl -sf -m 10 -H "Authorization: Bearer $VLLM_KEY" "$VLLM_URL/models" >/dev/null; then
    echo "WARNING: CT $CTID cannot reach $VLLM_URL" >&2
    echo "  The backend will install, but chat will fail until this is fixed." >&2
    echo "  Check: routing between subnets, vLLM --host binding (must not be" >&2
    echo "  127.0.0.1), and any firewall on the vLLM host (port 8000)." >&2
    read -rp "Continue anyway? [y/N] " CONT </dev/tty
    [[ "${CONT,,}" == "y" ]] || die "aborted — fix vLLM reachability and re-run"
fi

# --- 5. seed config (locked down) ---------------------------------------------

echo "[5/8] writing default config (everything OFF except local chat)"
pct exec "$CTID" -- bash -c "cat > /etc/proxmox-ai/config.json" <<EOF
{
  "listen_host": "0.0.0.0",
  "listen_port": $BACKEND_PORT,
  "active_provider": "vllm-shared",
  "providers": [
    {
      "id": "vllm-shared",
      "type": "vllm",
      "name": "Shared vLLM Server",
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
    systemctl daemon-reload
    systemctl enable --now proxmox-ai
"

# --- 6. TLS termination on the host -------------------------------------------

echo "[6/8] setting up TLS proxy on port $TLS_PORT (PVE cert -> backend)"
command -v socat >/dev/null || { apt-get update -qq && apt-get install -y -qq socat; }
cat > /etc/systemd/system/proxmox-ai-tls.service <<EOF
[Unit]
Description=TLS termination for proxmox-ai backend (mixed-content fix)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/socat OPENSSL-LISTEN:$TLS_PORT,reuseaddr,fork,cert=/etc/pve/local/pve-ssl.pem,key=/etc/pve/local/pve-ssl.key,verify=0 TCP:${CT_IP_ONLY}:$BACKEND_PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now proxmox-ai-tls

# --- 7. UI patch + dpkg hook ---------------------------------------------------

echo "[7/8] patching PVE web UI"
mkdir -p /opt/proxmox-ai
cp -a "$SRC_DIR/." /opt/proxmox-ai/
chmod +x /opt/proxmox-ai/frontend/patch.sh /opt/proxmox-ai/frontend/unpatch.sh
echo "https://$PVE_NODE_IP:$TLS_PORT" > /opt/proxmox-ai/backend-url
BACKEND_URL="https://$PVE_NODE_IP:$TLS_PORT" /opt/proxmox-ai/frontend/patch.sh

echo "[8/8] installing dpkg re-patch hook"
cp -a "$SRC_DIR/frontend/99proxmox-ai-repatch" /etc/apt/apt.conf.d/99proxmox-ai-repatch

# --- done -----------------------------------------------------------------------

sleep 3
echo
if pct exec "$CTID" -- curl -sf "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null; then
    echo "=== install complete on $PVE_NODE_NAME ==="
    echo "Backend:  https://$PVE_NODE_IP:$TLS_PORT/health (TLS)  |  http://$CT_IP_ONLY:$BACKEND_PORT/health (direct)"
    echo "UI:       hard-refresh https://$PVE_NODE_IP:8006 and look for the AI button"
    echo
    echo "NEXT STEPS:"
    echo "  1. Browser TLS: import THIS host's CA (/etc/pve/pve-root-ca.pem) into"
    echo "     each browser — see docs/browser-tls.md. (Each PVE cluster has its"
    echo "     own CA; trusting the old server's CA does NOT cover this one.)"
    echo "  2. PVE API token for live cluster context — recommended: a dedicated"
    echo "     proxmox-ai@pve user with a least-privilege role, see docs/installation.md"
    echo "     Step 4 (quick-test fallback: pveum user token add root@pam proxmox-ai --privsep 0)"
    echo "     then set pve.api_token in /etc/proxmox-ai/config.json (CT $CTID)"
    echo "     and: pct exec $CTID -- systemctl restart proxmox-ai"
    echo "  3. Enable toggles/categories in AI -> Settings (root@pam) —"
    echo "     all execution is OFF by default."
else
    echo "WARNING: backend health check failed. Inspect with:"
    echo "  pct exec $CTID -- journalctl -u proxmox-ai -n 50"
fi
