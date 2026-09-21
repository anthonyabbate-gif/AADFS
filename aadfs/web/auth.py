"""Password protection for when the app is reachable from another device.

On localhost this app needs no authentication: the only thing that can reach it
is you. The moment it binds to an address a tablet or phone can reach, that
stops being true, and the app holds your contest history and bankroll.

So access control is a single shared password over HTTP Basic auth. Safari and
every other browser prompt for it natively and then remember it, which keeps the
Sunday-morning workflow to one tap. It is deliberately the simplest thing that
works: this is a single-user tool, not a service with accounts.

Basic auth sends the password reversibly encoded, so it is only safe over a
connection that is itself encrypted -- a private network such as Tailscale, or
HTTPS behind a reverse proxy. `guard_public_bind` refuses to start the server in
a configuration where that would not hold.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

#: Environment variable holding the shared password.
PASSWORD_ENV = "AADFS_PASSWORD"
USERNAME_ENV = "AADFS_USERNAME"
DEFAULT_USERNAME = "aadfs"

#: Addresses that only this machine can reach.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}

#: Paths served without a password, so the login prompt itself can render.
PUBLIC_PATHS = {"/favicon.ico"}


def configured_password() -> str | None:
    value = os.environ.get(PASSWORD_ENV, "").strip()
    return value or None


def configured_username() -> str:
    return os.environ.get(USERNAME_ENV, "").strip() or DEFAULT_USERNAME


def is_local_host(host: str) -> bool:
    return host in LOCAL_HOSTS


def guard_public_bind(host: str) -> str | None:
    """Return an error message if this bind would expose the app unprotected."""
    if is_local_host(host):
        return None
    if configured_password():
        return None
    return (
        f"Refusing to bind to {host} without a password.\n\n"
        f"Binding to anything other than localhost makes this app reachable by "
        f"other devices, and it holds your lineups and contest history. Set a "
        f"password first:\n\n"
        f"    export {PASSWORD_ENV}='something-long-and-random'\n\n"
        f"Then start the server again. Basic auth is only private over an "
        f"encrypted connection, so put this behind Tailscale or an HTTPS "
        f"reverse proxy rather than straight onto the open internet."
    )


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Require a shared password on every request."""

    def __init__(self, app, password: str, username: str = DEFAULT_USERNAME,
                 realm: str = "AADFS"):
        super().__init__(app)
        self._password = password
        self._username = username
        self._realm = realm

    def _challenge(self) -> PlainTextResponse:
        return PlainTextResponse(
            "Authentication required.",
            status_code=401,
            headers={"WWW-Authenticate": f'Basic realm="{self._realm}", charset="UTF-8"'},
        )

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        header = request.headers.get("Authorization", "")
        scheme, _, encoded = header.partition(" ")
        if scheme.lower() != "basic" or not encoded:
            return self._challenge()

        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return self._challenge()

        username, _, password = decoded.partition(":")
        # Compare both halves in constant time so neither leaks by timing.
        username_ok = secrets.compare_digest(username, self._username)
        password_ok = secrets.compare_digest(password, self._password)
        if not (username_ok and password_ok):
            return self._challenge()

        return await call_next(request)


def install(app) -> bool:
    """Add password protection if one is configured. Returns whether it was."""
    password = configured_password()
    if not password:
        return False
    app.add_middleware(
        BasicAuthMiddleware, password=password, username=configured_username()
    )
    return True
