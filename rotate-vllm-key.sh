#!/bin/bash
# Rotate the vLLM API key end-to-end:
#   1. generate a new key
#   2. update vllm.service on the vLLM host (CT 100) and restart vllm
#   3. update every provider api_key in proxmox-ai's config.json (CT 130) and restart
#
# Run ON THE PVE HOST (as root). LAN-only test systems: this is optional hygiene.
#
# Env overrides:
#   VLLM_CTID=100        CT running vLLM
#   AI_CTID=130          CT running proxmox-ai backend
#   SERVICE_PATH=/etc/systemd/system/vllm.service
set -euo pipefail

VLLM_CTID="${VLLM_CTID:-100}"
AI_CTID="${AI_CTID:-130}"
SERVICE_PATH="${SERVICE_PATH:-/etc/systemd/system/vllm.service}"

die() { echo "ERROR: $*" >&2; exit 1; }
command -v pct >/dev/null || die "pct not found — run this on a Proxmox VE host as root"
[[ $EUID -eq 0 ]] || die "run as root"
pct status "$VLLM_CTID" >/dev/null 2>&1 || die "vLLM CT $VLLM_CTID not found"
pct status "$AI_CTID" >/dev/null 2>&1 || die "proxmox-ai CT $AI_CTID not found"

NEWKEY=$(openssl rand -hex 24)
echo "=== rotating vLLM API key ==="
echo "new key: ${NEWKEY:0:8}… (full value is written to configs, not printed)"

# --- 1. vLLM service ---
echo "[1/3] updating $SERVICE_PATH in CT $VLLM_CTID"
pct exec "$VLLM_CTID" -- bash -c "
    set -e
    grep -q -- '--api-key' $SERVICE_PATH || { echo 'no --api-key in $SERVICE_PATH' >&2; exit 1; }
    sed -i -E 's/--api-key [^ ]+/--api-key $NEWKEY/' $SERVICE_PATH
    systemctl daemon-reload
    systemctl restart vllm
"

# --- 2. wait for vLLM to come back ---
echo "[2/3] waiting for vLLM to accept the new key"
VLLM_URL=$(pct exec "$AI_CTID" -- python3 -c "
import json
c = json.load(open('/etc/proxmox-ai/config.json'))
print(next((p['base_url'] for p in c['providers'] if p.get('enabled')), ''))
")
[[ -n "$VLLM_URL" ]] || die "no enabled provider found in proxmox-ai config"
for i in $(seq 1 30); do
    if pct exec "$AI_CTID" -- curl -sf -m 5 -H "Authorization: Bearer $NEWKEY" "$VLLM_URL/models" >/dev/null 2>&1; then
        break
    fi
    [[ $i -eq 30 ]] && die "vLLM did not come back with the new key — check: pct exec $VLLM_CTID -- journalctl -u vllm -n 50"
    sleep 5
done

# --- 3. proxmox-ai config ---
echo "[3/3] updating provider api_key in CT $AI_CTID config"
pct exec "$AI_CTID" -- bash -c "
    python3 - <<'EOF'
import json
p = '/etc/proxmox-ai/config.json'
c = json.load(open(p))
n = 0
for pr in c.get('providers', []):
    if pr.get('api_key'):
        pr['api_key'] = '$NEWKEY'
        n += 1
open(p, 'w').write(json.dumps(c, indent=2))
print(f'updated {n} provider(s)')
EOF
    chmod 600 /etc/proxmox-ai/config.json
    systemctl restart proxmox-ai
"

echo ""
echo "=== rotation complete ==="
echo "verify chat works in the AI panel; old key is now invalid"
