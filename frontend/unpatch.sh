#!/bin/bash
# Remove the proxmox-ai UI patch and restore the original pvemanagerlib.js.
set -euo pipefail

PVE_LIB="/usr/share/pve-manager/js/pvemanagerlib.js"
AI_DIR="/usr/share/pve-manager/js/ai"

if [[ -f "${PVE_LIB}.proxmox-ai.bak" ]]; then
    cp -a "${PVE_LIB}.proxmox-ai.bak" "$PVE_LIB"
    echo "[proxmox-ai] restored original pvemanagerlib.js"
else
    echo "[proxmox-ai] no backup found; removing inject block manually"
    sed -i '/\/\* proxmox-ai-inject \*\//,/\/\* proxmox-ai-inject \*\//d' "$PVE_LIB"
fi

rm -rf "$AI_DIR"
echo "[proxmox-ai] unpatched."
