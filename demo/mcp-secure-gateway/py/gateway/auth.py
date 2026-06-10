"""
gateway/auth.py
===============
Caller authentication.

Every request to the gateway must carry a token that the gateway issued for
itself. The Model Context Protocol authorization guidance is firm on two points
that this models in miniature. A token must be validated as intended for this
audience, and a server must never pass a token it received straight through to
a downstream service. The gateway authenticates the caller, resolves it to a
principal, and from that point on reasons about the principal, not the token.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class AuthError(Exception):
    """Raised when a token is missing, unknown, or not issued for this gateway."""


@dataclass
class Authenticator:
    """A minimal token store: gateway-issued token to principal identity.

    A real deployment would validate an OAuth 2.1 access token, check its
    audience and expiry, and reject anything not minted for this resource. The
    contract here is the same: an unrecognised token is refused, full stop.
    """

    _tokens: dict[str, str] = field(default_factory=dict)

    def issue(self, principal: str, token: str) -> None:
        self._tokens[token] = principal

    def authenticate(self, token: str | None) -> str:
        if not token:
            raise AuthError("no token presented")
        principal = self._tokens.get(token)
        if principal is None:
            raise AuthError("token not recognised for this gateway")
        return principal
