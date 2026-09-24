#!/bin/bash
# proxmox-ai uninstaller. Run ON THE PVE HOST (as root).
# Removes: UI patch, dpkg hook, TLS proxy, backend CT (optional),
# host authorized_keys entry, and /opt/proxmox-ai.
#
# Env vars:
#   CTID=130          backend CT id
#   DESTROY_CT=0      set to 1 to also destroy the backend CT (default: just stop)
#   KEEP_KEYS=0       set to 1 to keep the host authorized_keys entry
set -euo pipefail

CTID="${CTID:-130}"
DESTROY_CT="${DESTROY_CT:-0}"
KEEP_KEYS="${KEEP_KEYS:-0}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "ERROR: $*" >&2; exit 1; }
command -v pct >/dev/null || die "pct not found — run this on a Proxmox VE host as root"
[[ $EUID -eq 0 ]] || die "run as root"

echo "=== proxmox-ai uninstaller ==="

# --- 1. remove UI patch ---
echo "[1/6] removing UI patch"
if [[ -x /opt/proxmox-ai/frontend/unpatch.sh ]]; then
    /opt/proxmox-ai/frontend/unpatch.sh || echo "  (unpatch failed — check pvemanagerlib.js manually)"
elif [[ -x "$SRC_DIR/frontend/unpatch.sh" ]]; then
    "$SRC_DIR/frontend/unpatch.sh" || true
else
    echo "  (unpatch.sh not found — restore pvemanagerlib.js.proxmox-ai.bak manually)"
fi

# --- 2. remove dpkg re-patch hook ---
echo "[2/6] removing dpkg hook"
rm -f /etc/apt/apt.conf.d/99proxmox-ai-repatch

# --- 3. stop + remove TLS proxy ---
echo "[3/6] removing TLS proxy"
systemctl disable --now proxmox-ai-tls 2>/dev/null || true
rm -f /etc/systemd/system/proxmox-ai-tls.service
systemctl daemon-reload

# --- 4. remove host authorized_keys entry ---
if [[ "$KEEP_KEYS" != "1" ]]; then
    echo "[4/6] removing CT SSH pubkey from /root/.ssh/authorized_keys"
    if pct status "$CTID" >/dev/null 2>&1; then
        PUBKEY=$(pct exec "$CTID" -- cat /etc/proxmox-ai/keys/id_ed25519.pub 2>/dev/null || true)
        if [[ -n "$PUBKEY" ]]; then
            KEY_BLOB=$(awk '{print $2}' <<<"$PUBKEY")
            grep -v "$KEY_BLOB" /root/.ssh/authorized_keys > /root/.ssh/authorized_keys.tmp || true
            mv /root/.ssh/authorized_keys.tmp /root/.ssh/authorized_keys
            chmod 600 /root/.ssh/authorized_keys
        fi
    else
        echo "  (CT $CTID not found — remove the key manually if needed)"
    fi
else
    echo "[4/6] KEEP_KEYS=1 — leaving authorized_keys untouched"
fi

# --- 5. stop / destroy backend CT ---
if pct status "$CTID" >/dev/null 2>&1; then
    if [[ "$DESTROY_CT" == "1" ]]; then
        echo "[5/6] destroying CT $CTID"
        pct stop "$CTID" 2>/dev/null || true
        sleep 2
        pct destroy "$CTID"
    else
        echo "[5/6] stopping CT $CTID (DESTROY_CT=1 to destroy)"
        pct stop "$CTID" 2>/dev/null || true
    fi
else
    echo "[5/6] CT $CTID not present"
fi

# --- 6. remove host-side files ---
echo "[6/6] removing /opt/proxmox-ai"
rm -rf /opt/proxmox-ai

echo ""
echo "=== uninstall complete ==="
echo "Manual leftovers to check:"
echo "  - PVE API token:  pveum user token remove root@pam proxmox-ai"
echo "  - CT $CTID $( [[ "$DESTROY_CT" == "1" ]] && echo '(destroyed)' || echo '(stopped, not destroyed — set DESTROY_CT=1)')"
echo "  - hard-refresh the PVE UI (Ctrl+Shift+R) to drop cached JS"
