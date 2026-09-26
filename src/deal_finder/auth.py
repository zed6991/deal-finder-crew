"""A single shared password for the hosted app, and the cron secret.

The password lives in APP_PASSWORD. Signing in sets a signed cookie that lasts
90 days; changing the password signs everyone out.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from deal_finder.db import ON_VERCEL

COOKIE = "df_session"
LIFETIME = 90 * 24 * 3600

SETUP_PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deal Finder</title>
<body style="font:17px -apple-system,BlinkMacSystemFont,sans-serif;background:#f2f2f7;margin:0;display:grid;place-items:center;min-height:100vh">
<div style="background:#fff;border-radius:18px;padding:28px;max-width:420px;margin:16px">
<h1 style="margin:0 0 8px;font-size:24px">One more step</h1>
<p style="color:#6e6e73;line-height:1.4">Add an <b>APP_PASSWORD</b> environment variable in your Vercel
project settings, then redeploy. The password keeps your saved items, sizes and any paid searches to yourself.</p>
</div></body>"""


def password() -> str:
    return os.environ.get("APP_PASSWORD", "")


def configured() -> bool:
    return bool(password())


def required() -> bool:
    """On Vercel the app is public on the internet, so a password is required."""
    return ON_VERCEL


def _key() -> bytes:
    return hashlib.sha256(b"deal-finder-session:" + password().encode()).digest()


def _sign(expires: int) -> str:
    return hmac.new(_key(), str(expires).encode(), hashlib.sha256).hexdigest()


def token(now: float | None = None) -> str:
    expires = int((now or time.time()) + LIFETIME)
    return f"{expires}.{_sign(expires)}"


def valid(cookie: str | None, now: float | None = None) -> bool:
    if not cookie or "." not in cookie:
        return False
    expires, _, sig = cookie.partition(".")
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    return hmac.compare_digest(sig, _sign(int(expires)))


def check_password(given: str) -> bool:
    return configured() and hmac.compare_digest(given.encode(), password().encode())


def set_cookie(response) -> None:
    response.set_cookie(
        COOKIE, token(), max_age=LIFETIME, httponly=True, samesite="lax",
        secure=ON_VERCEL, path="/",
    )


def cron_ok(header: str | None) -> bool:
    """Vercel sends `Authorization: Bearer $CRON_SECRET` with each cron call."""
    secret = os.environ.get("CRON_SECRET", "")
    return bool(secret) and hmac.compare_digest((header or "").encode(), f"Bearer {secret}".encode())
