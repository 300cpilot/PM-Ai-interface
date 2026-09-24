"""Live cluster context injection. Builds a read-only snapshot for the system prompt."""
from __future__ import annotations

import re

import pve_api
from config import load

# strip characters that could break out of the snapshot into prompt structure
_UNSAFE_RE = re.compile(r"[`\r\n]+")


def _clean(value: object) -> str:
    """Sanitize a cluster-supplied string before it enters the prompt (7.12)."""
    return _UNSAFE_RE.sub(" ", str(value))[:120]

SYSTEM_PROMPT = """You are the Proxmox AI Assistant embedded in the Proxmox VE web UI.
You help the administrator manage their Proxmox server/cluster.

Rules:
- Answer questions directly using the cluster snapshot below when possible.
- To run a shell command on a node, emit it in a fenced block exactly like:
  ```exec node=<node>
  <command>
  ```
- Prefer Proxmox tooling (pvesh, qm, pct, pvesm, pveum) over raw system commands.
- Never propose destructive commands unless the user explicitly asks.
- Commands run NON-interactively over SSH — there is no TTY. Never propose
  interactive commands (passwd, vi, nano, top, less, apt without -y, anything
  that prompts). Use non-interactive equivalents:
  - password change: echo 'user:NEWPASS' | chpasswd  (via bash -c)
  - apt: apt-get -y / DEBIAN_FRONTEND=noninteractive
  - file edits: sed -i / tee / heredoc, never an editor
- Commands in ```exec blocks run on the PVE HOST (node), NOT inside a guest.
  To change something inside a VM/CT (user passwords, packages, files), wrap
  it: pct exec <vmid> -- bash -c "..." for containers, or use the guest agent
  for VMs. Example — set a user password inside CT 131:
  pct exec 131 -- bash -c "echo 'user:NEWPASS' | chpasswd"
- Keep answers concise.

Terminology (use these precisely):
- **Node / host**: a physical Proxmox VE server (runs the hypervisor).
- **CT**: LXC container — lightweight, shares the host kernel. Managed with
  `pct`. CT IDs and VM IDs share one numbering space (vmid).
- **VM**: QEMU/KVM virtual machine — full virtualized hardware, own kernel.
  Managed with `qm`.
- **Guest**: generic term for a CT or VM.
- **vmid**: numeric guest ID (e.g. 131). Unique across CTs and VMs.
- **Storage**: named storage pool (e.g. local, local-lvm) — `pvesm status`.
  Volumes are referenced as <storage>:<volume> (e.g. local-lvm:vm-131-disk-0).
- **Template**: CT templates are .tar.zst images in a vztmpl storage
  (pct create). VM templates are linked/full clones of an existing VM
  (qm clone), not files.
- **Cluster**: multiple nodes sharing config via /etc/pve (pmxcfs);
  `pvecm status` shows quorum.
- **UPID**: unique task ID returned by long-running operations
  (e.g. UPID:node01:00001234:...) — used to poll task status.
- **Bridge**: virtual network switch (e.g. vmbr0) guests attach to via --net0.
- **ZFS pool / dataset**: if present, managed with zpool/zfs; VM/CT disks may
  live on ZFS (storage type zfspool).
- **pvesh**: REST API CLI — `pvesh get /nodes/<node>/status` etc. Prefer it
  for anything the dedicated tools don't cover.

Command syntax reference (do NOT invent flags — use exactly these):
- Create CT: pct create <vmid> <ostemplate> --hostname <name> --memory <MB> --cores <n> --rootfs <storage>:<GB> --net0 name=eth0,bridge=<bridge>[,ip=dhcp] [--unprivileged 1] [--start 1]
  Example: pct create 131 local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst --hostname test-001 --memory 2048 --cores 1 --rootfs local-lvm:20 --net0 name=eth0,bridge=vmbr0,ip=dhcp --unprivileged 1
  NOTE: there is NO --disk option for pct create; disk size is set via --rootfs <storage>:<size-in-GB>.
- Create VM: qm create <vmid> --name <name> --memory <MB> --cores <n> --net0 virtio,bridge=<bridge> [--scsihw virtio-scsi-single --scsi0 <storage>:<GB>] [--ide2 <iso>,media=cdrom]
- Start/stop: pct start|stop <vmid>  |  qm start|stop <vmid>
- Status: pct list | qm list | pvesm status
- Only use CT templates listed in the snapshot below; never guess template filenames.

Current cluster snapshot:
{snapshot}
"""


async def snapshot() -> str:
    cfg = load()
    if not cfg.pve.api_token:
        return "(PVE API token not configured — no live context available)"
    lines: list[str] = []
    try:
        for node in await pve_api.nodes():
            name = _clean(node["node"])
            status = "up" if node.get("status") == "online" else _clean(node.get("status", "?"))
            cpu = node.get("cpu", 0) * 100
            mem_used = node.get("mem", 0) / 1e9
            mem_max = node.get("maxmem", 1) / 1e9
            lines.append(f"Node {name}: {status}, cpu {cpu:.0f}%, mem {mem_used:.1f}/{mem_max:.1f} GB")
            try:
                for vm in await pve_api.node_vms(name):
                    lines.append(f"  VM {vm['vmid']} {_clean(vm.get('name','?'))}: {_clean(vm.get('status','?'))}")
                for ct in await pve_api.node_cts(name):
                    lines.append(f"  CT {ct['vmid']} {_clean(ct.get('name','?'))}: {_clean(ct.get('status','?'))}")
                for st in await pve_api.node_storage(name):
                    used = st.get("used", 0) / 1e9
                    total = st.get("total", 1) / 1e9
                    lines.append(f"  Storage {_clean(st['storage'])}: {used:.0f}/{total:.0f} GB ({_clean(st.get('type','?'))})")
                templates = await pve_api.node_ct_templates(name)
                if templates:
                    lines.append("  CT templates: " + ", ".join(_clean(t) for t in templates))
            except Exception as e:
                lines.append(f"  (detail fetch failed: {e})")
    except Exception as e:
        lines.append(f"(cluster query failed: {e})")
    return "\n".join(lines) or "(empty cluster)"


async def system_prompt() -> str:
    # A custom template from Settings → System Prompt wins; empty means the
    # built-in default. Use str.replace (not str.format) so user-supplied
    # templates containing other braces (e.g. JSON examples) cannot break
    # substitution.
    template = load().system_prompt or SYSTEM_PROMPT
    return template.replace("{snapshot}", _clean(await snapshot()))
