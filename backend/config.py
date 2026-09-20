"""Configuration management for proxmox-ai backend.

Config lives at /etc/proxmox-ai/config.json (chmod 600) in production.
Override with PROXMOX_AI_CONFIG env var for development.
API keys are write-only over the API: masked in all GET responses.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

CONFIG_PATH = Path(os.environ.get("PROXMOX_AI_CONFIG", "/etc/proxmox-ai/config.json"))
KILL_SWITCH_PATH = Path(os.environ.get("PROXMOX_AI_DISABLED", "/etc/proxmox-ai/DISABLED"))
AUDIT_LOG = Path(os.environ.get("PROXMOX_AI_AUDIT", "/var/log/proxmox-ai/audit.log"))
SESSIONS_DB = Path(os.environ.get("PROXMOX_AI_SESSIONS", "/var/lib/proxmox-ai/sessions.db"))

SSHMode = Literal["off", "confirm", "allowlist", "auto"]
CategoryMode = Literal["off", "confirm", "allow"]

CATEGORIES = [
    "read_status",
    "vmct_lifecycle",
    "storage",
    "network_firewall",
    "users",
    "host_system",
    "guest_exec",
]

LOCAL_PROVIDER_TYPES = {"vllm", "ollama", "llamacpp"}
ONLINE_PROVIDER_TYPES = {"openai", "anthropic", "openrouter", "compatible"}


class ProviderConfig(BaseModel):
    id: str
    type: str  # vllm | ollama | llamacpp | openai | anthropic | openrouter | compatible
    name: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    enabled: bool = True


class NodeConfig(BaseModel):
    name: str
    host: str
    ssh_user: str = "root"
    ssh_port: int = 22
    ssh_key: str = "/etc/proxmox-ai/keys/id_ed25519"


class PVEConfig(BaseModel):
    api_url: str = "https://127.0.0.1:8006/api2/json"
    api_token: str = ""  # e.g. root@pam!proxmox-ai=xxxxxxxx
    verify_tls: bool = False


class SecurityConfig(BaseModel):
    server_access: bool = False
    internet_access: bool = False
    online_providers: bool = False
    guest_exec: bool = False
    ssh_mode: SSHMode = "off"
    categories: dict[str, CategoryMode] = Field(
        default_factory=lambda: {c: "off" for c in CATEGORIES}
    )
    max_commands_per_minute: int = 10
    session_max_minutes: int = 120


class Config(BaseModel):
    listen_host: str = "0.0.0.0"
    listen_port: int = 9000
    active_provider: str = ""
    providers: list[ProviderConfig] = Field(default_factory=list)
    nodes: list[NodeConfig] = Field(default_factory=list)
    pve: PVEConfig = Field(default_factory=PVEConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)


_lock = threading.Lock()
_config: Config | None = None


def load() -> Config:
    global _config
    with _lock:
        if _config is not None:
            return _config
        if CONFIG_PATH.exists():
            _config = Config.model_validate(json.loads(CONFIG_PATH.read_text()))
        else:
            _config = Config()
        return _config


def save(cfg: Config) -> None:
    global _config
    with _lock:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(cfg.model_dump_json(indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(CONFIG_PATH)
        _config = cfg


def reload() -> Config:
    global _config
    with _lock:
        _config = None
    return load()


def masked(cfg: Config) -> dict[str, Any]:
    """Config dict with API keys masked for UI responses."""
    data = cfg.model_dump()
    for p in data["providers"]:
        if p.get("api_key"):
            p["api_key"] = "********"
    if data["pve"].get("api_token"):
        data["pve"]["api_token"] = "********"
    return data


def get_provider(cfg: Config, provider_id: str | None = None) -> ProviderConfig | None:
    pid = provider_id or cfg.active_provider
    for p in cfg.providers:
        if p.id == pid and p.enabled:
            return p
    return None


def kill_switch_active() -> bool:
    return KILL_SWITCH_PATH.exists()
