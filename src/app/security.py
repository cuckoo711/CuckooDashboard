"""Request guards and cache policy for the Dashboard web application."""

from __future__ import annotations

import ipaddress
import os
import secrets
from urllib.parse import urlparse

from flask import Flask, abort, request

from core.credentials import VaultError, get_global_secret

_DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN") or secrets.token_urlsafe(24)


def get_dashboard_token() -> str:
    """Return the mutation-protection token without exposing Vault failures."""
    try:
        token = get_global_secret("dashboard_token", "")
    except VaultError:
        token = ""
    return str(token or os.environ.get("DASHBOARD_TOKEN") or _DASHBOARD_TOKEN)


def same_site_from_header(value: str | None) -> bool:
    """Return whether an Origin/Referer value targets the current Dashboard origin."""
    if not value:
        return False
    try:
        parsed = urlparse(value)
        host_url = urlparse(request.host_url)
        return parsed.scheme == host_url.scheme and parsed.netloc == host_url.netloc
    except Exception:
        return False


def require_post_protection() -> None:
    """Preserve the existing same-origin/token guard for state-changing POST requests."""
    if request.method != "POST":
        return
    expected = get_dashboard_token()
    provided = request.headers.get("X-Dashboard-Token")
    if provided and secrets.compare_digest(provided, expected):
        return
    if same_site_from_header(request.headers.get("Origin")):
        return
    if same_site_from_header(request.headers.get("Referer")):
        return
    abort(403)


def _is_trusted_loopback_host(value: str | None) -> bool:
    """Reject DNS-rebinding Host values on loopback-only management surfaces."""
    if not value:
        return False
    try:
        hostname = (urlparse(f"//{value}").hostname or "").rstrip(".").lower()
        if hostname == "localhost":
            return True
        address = ipaddress.ip_address(hostname)
        if address.is_loopback:
            return True
        return bool(
            isinstance(address, ipaddress.IPv6Address)
            and address.ipv4_mapped
            and address.ipv4_mapped.is_loopback
        )
    except (ValueError, TypeError):
        return False


def require_loopback_access() -> None:
    """Restrict local management surfaces to loopback peers and trusted Host values."""
    remote_addr = request.remote_addr
    try:
        address = ipaddress.ip_address(remote_addr) if remote_addr else None
        is_loopback = bool(address and address.is_loopback)
        if not is_loopback and isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            is_loopback = address.ipv4_mapped.is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback or not _is_trusted_loopback_host(request.host):
        abort(403, description="settings is only available from loopback")


def register_security_hooks(app: Flask) -> None:
    """Install request guards and the existing no-cache response policy."""

    @app.before_request
    def protect_settings_static_assets():
        if request.path.startswith("/static/settings"):
            require_loopback_access()

    @app.after_request
    def no_cache_static(response):
        if (
            request.path.startswith("/static/")
            or request.path.startswith("/settings")
            or request.path.startswith("/api/settings")
            or request.path == "/"
        ):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response
