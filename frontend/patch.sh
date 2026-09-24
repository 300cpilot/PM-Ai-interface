#!/bin/bash
# proxmox-ai UI patcher — idempotent.
# Injects the AI panel into pvemanagerlib.js and installs static assets.
# Re-run after every pve-manager upgrade (handled by the dpkg hook).
set -euo pipefail

PVE_LIB="/usr/share/pve-manager/js/pvemanagerlib.js"
AI_DIR="/usr/share/pve-manager/js/ai"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MARKER="/* proxmox-ai-inject */"
# Resolve the backend URL (in priority order):
#   1. BACKEND_URL env var (used by install.sh)
#   2. persisted file written by install.sh (survives dpkg re-patch hook runs)
#   3. same-host HTTPS default (TLS proxy port)
URL_FILE="/opt/proxmox-ai/backend-url"
if [[ -z "${BACKEND_URL:-}" && -f "$URL_FILE" ]]; then
    BACKEND_URL="$(head -n1 "$URL_FILE" | tr -d '[:space:]')"
fi
BACKEND_URL="${BACKEND_URL:-https://127.0.0.1:9443}"

# cache-buster: pveproxy serves static JS with only Last-Modified (no
# Cache-Control/ETag), so browsers heuristically cache ai-*.js and can run
# stale code after a re-patch. Stamp a version into the loader and update it
# on every run so the assets are always re-fetched.
AI_VERSION="$(date +%s)"

echo "[proxmox-ai] patching $PVE_LIB"

if [[ ! -f "$PVE_LIB" ]]; then
    echo "[proxmox-ai] ERROR: $PVE_LIB not found — is pve-manager installed?" >&2
    exit 1
fi

# backup (once)
if [[ ! -f "${PVE_LIB}.proxmox-ai.bak" ]]; then
    cp -a "$PVE_LIB" "${PVE_LIB}.proxmox-ai.bak"
    echo "[proxmox-ai] backup created: ${PVE_LIB}.proxmox-ai.bak"
fi

# install static assets
mkdir -p "$AI_DIR"
cp -a "$SRC_DIR/ai-panel.js" "$SRC_DIR/ai-settings.js" "$AI_DIR/"

# inject loader (idempotent)
if grep -qF "$MARKER" "$PVE_LIB"; then
    echo "[proxmox-ai] already patched, refreshing assets + backend URL"
    sed -i "s|^window\.PVE_AI_BACKEND = \".*\";|window.PVE_AI_BACKEND = \"$BACKEND_URL\";|" "$PVE_LIB"
    # update cache-buster if this loader version supports it
    sed -i "s|^window\.PVE_AI_VERSION = \".*\";|window.PVE_AI_VERSION = \"$AI_VERSION\";|" "$PVE_LIB"
else
    cat >> "$PVE_LIB" <<EOF

$MARKER
window.PVE_AI_BACKEND = "$BACKEND_URL";
window.PVE_AI_VERSION = "$AI_VERSION";
(function() {
    var v = '?v=' + (window.PVE_AI_VERSION || '1');
    var load = function(src) {
        var s = document.createElement('script');
        s.src = src;
        document.head.appendChild(s);
    };
    load('/pve2/js/ai/ai-settings.js' + v);
    load('/pve2/js/ai/ai-panel.js' + v);
})();
$MARKER
EOF
    echo "[proxmox-ai] injected loader into pvemanagerlib.js"
fi

echo "[proxmox-ai] done. Hard-refresh the PVE web UI (Ctrl+Shift+R) to load the AI button."

# pveproxy maps static dirs at startup (add_dirs) — restart so /pve2/js/ai/ is served
if systemctl is-active --quiet pveproxy; then
    systemctl restart pveproxy
    echo "[proxmox-ai] pveproxy restarted to register $AI_DIR"
fi
