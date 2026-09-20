"""Security enforcement: category matrix, allow/deny lists, rate limits, kill switch.

Denylist commands ALWAYS require explicit approval, even in full-auto mode.
"""
from __future__ import annotations

import ipaddress
import re
import threading
import time
from collections import deque
from urllib.parse import urlparse

from config import Config, kill_switch_active

# Read-only verbs allowed to auto-run when ssh_mode == "allowlist"
ALLOWLIST_PATTERNS = [
    r"^pvesh\s+get\b",
    r"^qm\s+(list|status|config|agent\s+\d+\s+info)\b",
    r"^pct\s+(list|status|config)\b",
    r"^pvesm\s+status\b",
    r"^df\b",
    r"^free\b",
    r"^uptime\b",
    r"^uname\b",
    r"^ip\s+(addr|link|route)\s*(show)?\s*$",
    r"^systemctl\s+(status|is-active|is-enabled)\b",
    r"^journalctl\s+.*-n\s*\d*",
    r"^cat\s+/proc/(cpuinfo|meminfo|loadavg)\s*$",
    r"^lsblk\b",
    r"^zpool\s+(status|list|iostat)\b",
    r"^zfs\s+list\b",
    r"^pveversion\b",
    r"^pvecm\s+status\b",
]

# Catastrophic commands: ALWAYS require approval regardless of mode
DENYLIST_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)?/(\s|$|\*)",
    r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+.*\*",
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\b.*\bof=/dev/",
    r"\bzpool\s+destroy\b",
    r"\bzfs\s+destroy\b",
    r"\bwipefs\b",
    r"\b(shutdown|poweroff|reboot|halt)\b",
    r"\b(qm|pct)\s+destroy\b.*--purge",
    r"\biptables\s+-F\b",
    r"\bnft\s+flush\b",
    r"\bpasswd\b",
    r"\buserdel\b",
    r"\buseradd\b",
    r"\bvisudo\b",
    r">\s*/dev/sd[a-z]",
    r"\bchmod\s+-R\s+777\s+/\b",
]

# Map command prefixes to permission categories
CATEGORY_MAP: list[tuple[str, str]] = [
    (r"^(qm|pct)\s+(start|stop|shutdown|reboot|reset|suspend|resume|clone|migrate|create|destroy|set|resize|snapshot|rollback)", "vmct_lifecycle"),
    (r"^(pvesm|zfs|zpool|lvcreate|lvremove|mkfs|mount|umount)\b", "storage"),
    (r"^(ip\s+link\s+set|ifup|ifdown|iptables|nft|pve-firewall)\b", "network_firewall"),
    (r"^(pveum|useradd|userdel|passwd)\b", "users"),
    (r"^(qm\s+agent|pct\s+exec)\b", "guest_exec"),
    (r"^(pvesh\s+get|qm\s+(list|status|config)|pct\s+(list|status|config)|df|free|uptime|uname|systemctl\s+status|journalctl|lsblk|pveversion|pvecm\s+status|zpool\s+(status|list)|zfs\s+list|ip\s+(addr|route))", "read_status"),
]

_allowlist_re = [re.compile(p) for p in ALLOWLIST_PATTERNS]
_denylist_re = [re.compile(p) for p in DENYLIST_PATTERNS]
_category_re = [(re.compile(p), c) for p, c in CATEGORY_MAP]


class RateLimiter:
    def __init__(self) -> None:
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self, max_per_minute: int) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] > 60:
                self._hits.popleft()
            if len(self._hits) >= max_per_minute:
                return False
            self._hits.append(now)
            return True


rate_limiter = RateLimiter()


def is_denied(command: str) -> bool:
    return any(r.search(command) for r in _denylist_re)


def is_allowlisted(command: str) -> bool:
    return any(r.search(command.strip()) for r in _allowlist_re)


def categorize(command: str) -> str:
    for r, cat in _category_re:
        if r.search(command.strip()):
            return cat
    return "host_system"


def is_private_url(url: str) -> bool:
    """True if URL host is RFC1918/loopback/link-local."""
    try:
        host = urlparse(url).hostname or ""
        if host in ("localhost",):
            return True
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False  # hostname that isn't an IP: treat as external


def check_provider_allowed(cfg: Config, ptype: str, base_url: str) -> str | None:
    """Return an error string if the provider is blocked by toggles, else None."""
    sec = cfg.security
    if ptype in ("openai", "anthropic", "openrouter") and not sec.online_providers:
        return f"online_providers is off: provider type '{ptype}' is not allowed"
    if ptype == "compatible" and not sec.online_providers and not is_private_url(base_url):
        return "online_providers is off: only local (RFC1918) endpoints allowed"
    if base_url and not sec.internet_access and not is_private_url(base_url):
        return f"internet_access is off: '{base_url}' is not a private address"
    return None


def check_execution(cfg: Config, command: str, category: str | None = None) -> tuple[str, str]:
    """Decide what happens to a proposed command.

    Returns (decision, reason) where decision is one of:
      "blocked"  — hard no (kill switch, server_access off, category off, ssh off)
      "confirm"  — requires explicit approval
      "auto"     — may execute immediately
    """
    cat = category or categorize(command)
    sec = cfg.security

    if kill_switch_active():
        return "blocked", "kill switch active (/etc/proxmox-ai/DISABLED)"
    if not sec.server_access:
        return "blocked", "server_access is off"
    if not rate_limiter.allow(sec.max_commands_per_minute):
        return "blocked", f"rate limit exceeded ({sec.max_commands_per_minute}/min)"
    if is_denied(command):
        return "confirm", "denylist: destructive command always requires approval"

    cat_mode = sec.categories.get(cat, "off")
    if cat == "guest_exec" and not sec.guest_exec:
        return "blocked", "guest_exec toggle is off"
    if cat_mode == "off":
        return "blocked", f"category '{cat}' is off"
    if cat_mode == "confirm":
        return "confirm", f"category '{cat}' requires approval"

    # category allows; raw shell still governed by ssh_mode
    if sec.ssh_mode == "off":
        return "blocked", "ssh_mode is off"
    if sec.ssh_mode == "confirm":
        return "confirm", "ssh_mode=confirm"
    if sec.ssh_mode == "allowlist":
        if is_allowlisted(command):
            return "auto", "allowlisted read-only command"
        return "confirm", "not on allowlist"
    return "auto", "ssh_mode=auto"
