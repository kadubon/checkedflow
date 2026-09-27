"""Trusted transport identities for policy checks; no client-supplied role claims."""

import hmac
from collections.abc import Callable

from checkedflow.agents.access import LOCAL, Principal
from checkedflow.agents.oauth import OAuth
from checkedflow.core.values import Failure, require


class Authentication:
    def __init__(self, token: str, oauth: OAuth | None = None) -> None:
        require(
            oauth is not None or (len(token) >= 32 and all(33 <= ord(c) <= 126 for c in token)),
            "AUTH",
            "a printable 32+ character operator token or OAuth is required",
        )
        self.expected, self.oauth = ("Bearer " + token).encode(), oauth

    def verify(self, authorization: str) -> Principal | None:
        if self.oauth is None:
            return LOCAL if hmac.compare_digest(authorization.encode(), self.expected) else None
        if not authorization.startswith("Bearer "):
            return None
        token = self.oauth.verify(authorization[7:])
        if token is None or "checkedflow" not in token.scopes:
            return None
        return Principal(
            self.oauth.issuer, token.client_id, token.subject or "", token.expires_at or 0
        )


def authenticated_headers(
    values: list[str], verifier: Callable[[str], Principal | None]
) -> Principal:
    require(
        len(values) == 1 and isinstance(values[0], str),
        "ACCESS",
        "one authorization header required",
    )
    principal = verifier(values[0])
    if principal is None:
        raise Failure("ACCESS", "client authentication denied")
    return principal


def oauth_principal() -> Principal | None:
    from mcp.server.auth.middleware.auth_context import get_access_token

    token = get_access_token()
    if token is None or "checkedflow" not in token.scopes:
        return None
    issuer = (token.claims or {}).get("iss")
    if not isinstance(issuer, str) or not issuer or not token.subject or not token.expires_at:
        return None
    return Principal(issuer, token.client_id, token.subject, token.expires_at)
