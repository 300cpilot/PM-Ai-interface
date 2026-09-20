"""Append-only audit log (JSONL). Every security-relevant event lands here."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from config import AUDIT_LOG

_lock = threading.Lock()


def log(event: str, actor: str = "system", **fields: Any) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actor": actor,
        **fields,
    }
    line = json.dumps(record, default=str)
    with _lock:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        new = not AUDIT_LOG.exists()
        with AUDIT_LOG.open("a") as f:
            f.write(line + "\n")
        if new:
            os.chmod(AUDIT_LOG, 0o600)


def read_tail(limit: int = 200) -> list[dict[str, Any]]:
    if not AUDIT_LOG.exists():
        return []
    with AUDIT_LOG.open() as f:
        lines = f.readlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
