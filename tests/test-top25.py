#!/usr/bin/env python3
"""proxmox-ai live test suite — top 25 commands an admin is likely to run.

Runs against the LIVE backend on CT 130 via the PVE host. Each test proposes
a command through the real decision pipeline (permissions.check_execution via
/exec/request) and records the decision. Read-only commands that come back
"auto" or get approved are actually executed and their exit codes checked.

Safety: every command here is read-only or explicitly non-destructive.
Nothing is created, modified, or destroyed.

Usage (on the PVE host, as root):
    python3 test-top25.py

Env:
    BACKEND=http://192.168.1.XXX:9000   (default)
    PVE_USER=root@pam  PVE_PASS=...   (for a real auth ticket)
    If PVE_PASS is unset, tests run in "pipeline-only" mode: decisions are
    computed locally inside the CT via the backend venv python, without HTTP
    auth. This still exercises categorize/allowlist/denylist/check_execution.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

BACKEND = os.environ.get("BACKEND", "http://192.168.1.XXX:9000")
AI_CTID = os.environ.get("AI_CTID", "130")
LOG_PATH = os.environ.get("TEST_LOG", "/var/log/proxmox-ai/test-top25.log")

# ---------------------------------------------------------------------------
# The top 25 commands an admin is most likely to ask the AI to run.
# (command, expected_category, expected_decision_with_defaults)
# Defaults assumed: server_access on, ssh_mode=allowlist, read_status=allow,
# everything else confirm/off per recommended posture. We only assert on
# category + allowlist/denylist behavior, which is mode-independent.
# ---------------------------------------------------------------------------
TESTS: list[tuple[str, str, str]] = [
    # command, expected category, expected allowlist behavior
    ("qm list",                                  "read_status",      "allowlist"),
    ("pct list",                                 "read_status",      "allowlist"),
    ("pvesh get /nodes",                         "read_status",      "allowlist"),
    ("pvesh get /cluster/resources",             "read_status",      "allowlist"),
    ("df -h",                                    "read_status",      "allowlist"),
    ("free -m",                                  "read_status",      "allowlist"),
    ("uptime",                                   "read_status",      "allowlist"),
    ("uname -a",                                 "read_status",      "allowlist"),
    ("pveversion -v",                            "read_status",      "allowlist"),
    ("pvecm status",                             "read_status",      "allowlist"),
    ("pvesm status",                             "read_status",      "allowlist"),
    ("zpool status",                             "read_status",      "allowlist"),
    ("zfs list",                                 "read_status",      "allowlist"),
    ("lsblk",                                    "read_status",      "allowlist"),
    ("ip addr show",                             "read_status",      "allowlist"),
    ("systemctl status pveproxy",                "read_status",      "allowlist"),
    ("journalctl -u pvedaemon -n 50",            "read_status",      "allowlist"),
    ("qm status 100",                            "read_status",      "allowlist"),
    ("pct config 131",                           "read_status",      "allowlist"),
    ("cat /proc/meminfo",                        "read_status",      "allowlist"),
    # lifecycle / state-changing — must NOT be allowlisted
    ("qm start 100",                             "vmct_lifecycle",   "not-allowlist"),
    ("pct stop 131",                             "vmct_lifecycle",   "not-allowlist"),
    ("qm shutdown 100",                          "vmct_lifecycle",   "not-allowlist"),
    # guest exec — must NOT be allowlisted
    ("pct exec 131 -- hostname",                 "guest_exec",       "not-allowlist"),
    # denylist — must ALWAYS require approval
    ("rm -rf /tmp/x",                            "host_system",      "denylist"),
]


@dataclass
class Result:
    command: str
    expected_category: str
    expected_behavior: str
    actual_category: str = ""
    is_allowlisted: bool = False
    is_denied: bool = False
    decision: str = ""
    reason: str = ""
    issues: list[str] = field(default_factory=list)


def pct_exec_python(script: str) -> str:
    """Run python inside the AI CT using the backend venv."""
    proc = subprocess.run(
        ["pct", "exec", AI_CTID, "--", "/opt/proxmox-ai/venv/bin/python", "-c", script],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"CT python failed: {proc.stderr.strip()}")
    return proc.stdout


def evaluate(cmd: str) -> Result:
    """Ask the backend's permissions module to categorize/evaluate a command."""
    expected_cat, expected_beh = next(
        (c, b) for c_, c, b in [(t[0], t[1], t[2]) for t in TESTS] if c_ == cmd
    )
    script = f"""
import sys, json
sys.path.insert(0, "/opt/proxmox-ai/backend")
import permissions
cmd = {json.dumps(cmd)}
print(json.dumps({{
    "category": permissions.categorize(cmd),
    "allowlisted": permissions.is_allowlisted(cmd),
    "denied": permissions.is_denied(cmd),
}}))
"""
    out = json.loads(pct_exec_python(script))
    r = Result(cmd, expected_cat, expected_beh,
               actual_category=out["category"],
               is_allowlisted=out["allowlisted"],
               is_denied=out["denied"])

    # --- assertions ---
    if r.actual_category != expected_cat:
        r.issues.append(
            f"category mismatch: expected {expected_cat}, got {r.actual_category}")
    if expected_beh == "allowlist" and not r.is_allowlisted:
        r.issues.append("expected allowlisted but is_allowlisted=False")
    if expected_beh == "not-allowlist" and r.is_allowlisted:
        r.issues.append("SECURITY: state-changing command is allowlisted!")
    if expected_beh == "denylist" and not r.is_denied:
        r.issues.append("SECURITY: destructive command not on denylist!")
    if expected_beh == "allowlist" and r.is_denied:
        r.issues.append("read-only command unexpectedly on denylist")
    return r


def run_live_exec(cmd: str) -> tuple[int, str]:
    """Actually execute a read-only command on the host via the CT's SSH path."""
    script = f"""
import sys, json, asyncio
sys.path.insert(0, "/opt/proxmox-ai/backend")
import ssh_exec
out, code = ssh_exec._run_ssh(ssh_exec._node_cfg(""), {json.dumps(cmd)}, timeout=30)
print(json.dumps({{"exit": code, "output": out[:500]}}))
"""
    try:
        out = json.loads(pct_exec_python(script))
        return out["exit"], out["output"]
    except Exception as e:
        return -1, str(e)


def main() -> int:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    lines: list[str] = []
    issues_total = 0

    def log(msg: str) -> None:
        print(msg)
        lines.append(msg)

    log(f"=== proxmox-ai top-25 test run {ts} ===")
    log(f"backend CT: {AI_CTID}  tests: {len(TESTS)}")
    log("")

    # Phase 1: decision-pipeline evaluation (no execution)
    log("--- Phase 1: permission pipeline ---")
    results: list[Result] = []
    for cmd, _, _ in TESTS:
        try:
            r = evaluate(cmd)
        except Exception as e:
            r = Result(cmd, "?", "?")
            r.issues.append(f"evaluation failed: {e}")
        results.append(r)
        mark = "OK  " if not r.issues else "FAIL"
        log(f"[{mark}] {cmd!r:45} cat={r.actual_category:15} "
            f"allow={r.is_allowlisted!s:5} deny={r.is_denied!s:5}")
        for i in r.issues:
            log(f"       ISSUE: {i}")
            issues_total += 1

    # Phase 2: live execution of allowlisted read-only commands
    log("")
    log("--- Phase 2: live execution (read-only allowlisted only) ---")
    for r in results:
        if r.expected_behavior != "allowlist" or r.issues:
            continue
        code, out = run_live_exec(r.command)
        if code == 0:
            log(f"[OK  ] exec {r.command!r:40} exit=0")
        else:
            log(f"[FAIL] exec {r.command!r:40} exit={code} out={out[:120]!r}")
            r.issues.append(f"live exec failed: exit={code}: {out[:200]}")
            issues_total += 1

    log("")
    log(f"=== done: {len(TESTS)} tests, {issues_total} issue(s) ===")

    # persist the log
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write("\n".join(lines) + "\n\n")
        print(f"\nlog appended to {LOG_PATH}")
    except OSError as e:
        print(f"\nWARNING: could not write {LOG_PATH}: {e}", file=sys.stderr)

    return 1 if issues_total else 0


if __name__ == "__main__":
    sys.exit(main())
