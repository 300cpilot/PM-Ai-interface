"""PVE session auth. The UI forwards the user's PVEAuthCookie; we validate it
against the PVE API and check admin privilege. Settings writes require root@pam.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from fastapi import Header, HTTPException

import audit
from config import load

# ticket validation cache: cookie -> (username, is_admin, expires)
_cache: dict[str, tuple[str, bool, float]] = {}
CACHE_TTL = 60.0


@dataclass
class User:
    name: str
    is_admin: bool
    is_root: bool


async def _validate_ticket(cookie: str, csrf: str | None) -> User:
    cfg = load()
    now = time.monotonic()
    if cookie in _cache:
        name, is_admin, exp = _cache[cookie]
        if now < exp:
            return User(name, is_admin, name == "root@pam")

    headers = {"Cookie": f"PVEAuthCookie={cookie}"}
    if csrf:
        headers["CSRFPreventionToken"] = csrf
    try:
        async with httpx.AsyncClient(verify=cfg.pve.verify_tls, timeout=10.0) as client:
            # any authenticated endpoint works; version is cheap
            resp = await client.get(f"{cfg.pve.api_url}/version", headers=headers)
            if resp.status_code in (401, 403):
                raise HTTPException(401, "invalid or expired PVE session")
            resp.raise_for_status()
            # who is this ticket?
            resp2 = await client.get(f"{cfg.pve.api_url}/access/permissions", headers=headers)
            username = "unknown"
            is_admin = False
            if resp2.status_code == 200:
                # permissions endpoint doesn't echo the user; use access/ticket username
                # embedded in the cookie itself: PVEAuthCookie=PVE:user@realm:...
                username = _username_from_cookie(cookie)
                perms = resp2.json().get("data", {})
                caps = perms.get("capabilities", {})
                is_admin = username == "root@pam" or _has_admin_caps(caps)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"cannot reach PVE API: {e}")

    _cache[cookie] = (username, is_admin, now + CACHE_TTL)
    return User(username, is_admin, username == "root@pam")


def _username_from_cookie(cookie: str) -> str:
    # PVEAuthCookie format: PVE:USER@REALM:TIMESTAMP::signature
    try:
        parts = cookie.split(":")
        if len(parts) >= 2 and parts[0] == "PVE":
            return parts[1]
    except Exception:
        pass
    return "unknown"


def _has_admin_caps(caps: dict) -> bool:
    # /access/permissions is keyed by ACL path ("/", "/vms/100", ...), not by
    # category — Sys.Modify + VM.Allocate at the root path "/" is effectively
    # cluster-wide admin (what Administrator/PVEAdmin grants there).
    root = caps.get("/", {})
    return bool(root.get("Sys.Modify") and root.get("VM.Allocate"))


def _extract_cookie(pve_auth_cookie: str | None, authorization: str | None) -> str:
    cookie = pve_auth_cookie or ""
    if not cookie and authorization and authorization.startswith("PVE "):
        cookie = authorization[4:]
    if not cookie:
        raise HTTPException(401, "missing PVEAuthCookie")
    return cookie


async def require_user(
    pve_auth_cookie: str | None = Header(default=None),
    csrf_prevention_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> User:
    cookie = _extract_cookie(pve_auth_cookie, authorization)
    user = await _validate_ticket(cookie, csrf_prevention_token)
    if not user.is_admin:
        audit.log("auth_denied", actor=user.name, reason="not admin")
        raise HTTPException(403, "PVE admin privileges required")
    return user


async def require_root_dep(
    pve_auth_cookie: str | None = Header(default=None),
    csrf_prevention_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> User:
    user = await require_user(pve_auth_cookie, csrf_prevention_token, authorization)
    if not user.is_root:
        audit.log("auth_denied", actor=user.name, reason="root@pam required")
        raise HTTPException(403, "root@pam required for settings changes")
    return user
