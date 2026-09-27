"""OAuth resource-server verification with operator-provisioned public JWKS keys."""

from pathlib import Path
from urllib.parse import urlsplit

import anyio
import jwt
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from checkedflow.core.values import Failure, array, obj, require, text
from checkedflow.wire import document


class OAuth:
    """Validate audience, issuer, expiry and asymmetric signatures; never follow token URLs.

    An external authorization server issues tokens. Its public JWKS file is installed by
    the operator and reread per request to support key rotation without trusting jku/x5u.
    """

    def __init__(self, issuer: str, audience: str, jwks: Path) -> None:
        require(urlsplit(issuer).scheme == "https", "AUTH", "OAuth issuer must use HTTPS")
        self.issuer, self.audience, self.jwks = issuer, audience, jwks
        self.settings = AuthSettings(
            issuer_url=AnyHttpUrl(issuer),
            resource_server_url=AnyHttpUrl(audience),
            required_scopes=["checkedflow"],
            validate_token_resource=True,
        )
        self.keys()  # Fail closed at startup on a missing or invalid public key file.

    def keys(self) -> dict[str, jwt.PyJWK]:
        with self.jwks.open("rb") as source:
            raw = source.read(65537)
        require(len(raw) <= 65536, "AUTH", "JWKS size limit")
        values = array(document(raw).get("keys"), limit=32)
        keys: dict[str, jwt.PyJWK] = {}
        for value in values:
            key = obj(value)
            kid = text(key.get("kid"))
            require(
                kid not in keys and "d" not in key and "k" not in key,
                "AUTH",
                "unique public asymmetric keys required",
            )
            require(
                key.get("alg") in {"RS256", "ES256", "EdDSA"} and key.get("use", "sig") == "sig",
                "AUTH",
                "unsupported signing key",
            )
            keys[kid] = jwt.PyJWK.from_dict(key)
        require(bool(keys), "AUTH", "at least one public key required")
        return keys

    def verify(self, token: str) -> AccessToken | None:
        try:
            if len(token) > 16384:
                return None
            header = jwt.get_unverified_header(token)
            key = self.keys().get(header.get("kid", ""))
            if key is None or header.get("alg") != key.algorithm_name:
                return None
            claims = jwt.decode(
                token,
                key.key,
                algorithms=[key.algorithm_name],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub", "client_id"]},
            )
            if not all(isinstance(claims.get(k), str) and claims[k] for k in ("sub", "client_id")):
                return None
            scope = claims.get("scope", "")
            if not isinstance(scope, str) or not isinstance(claims["exp"], int):
                return None
            return AccessToken(
                token=token,
                client_id=claims["client_id"],
                subject=claims["sub"],
                claims={"iss": self.issuer},
                scopes=scope.split(),
                expires_at=claims["exp"],
                resource=self.audience,
            )
        except (jwt.PyJWTError, Failure, OSError, ValueError, TypeError):
            return None

    async def verify_token(self, token: str) -> AccessToken | None:
        return await anyio.to_thread.run_sync(self.verify, token)
