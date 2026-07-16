"""Security guards shared by loopback-only Settings mutation routes."""

from __future__ import annotations

import secrets

from flask import abort, request

from app.security import get_dashboard_token, require_loopback_access, same_site_from_header

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def require_settings_access() -> None:
    """Require loopback access and same-origin or token proof for unsafe methods."""
    require_loopback_access()
    if request.method in _SAFE_METHODS:
        return
    provided = request.headers.get("X-Dashboard-Token")
    expected = get_dashboard_token()
    if provided and secrets.compare_digest(provided, expected):
        return
    if same_site_from_header(request.headers.get("Origin")):
        return
    if same_site_from_header(request.headers.get("Referer")):
        return
    abort(403)
