"""Pure committed identity values and authenticated context for v2 transitions."""

from dataclasses import dataclass

from checkedflow.core.values import integer, require, text

ROLES = frozenset({"administrator", "producer", "executor", "verifier", "effect_executor"})


@dataclass(frozen=True)
class Credential:
    identity: str
    revision: int
    organization: str
    role: str
    mission: str
    public_key: str
    activated_height: int
    retired_height: int | None = None
    revoked: bool = False

    def __post_init__(self) -> None:
        text(self.identity, limit=80)
        integer(self.revision, low=1)
        text(self.organization, limit=80)
        require(self.role in ROLES, "AUTHORITY", "unsupported signing purpose")
        require(
            (self.role == "administrator" and self.mission == "")
            or (self.role != "administrator" and 0 < len(self.mission) <= 80),
            "SCOPE",
            "non-administrative keys must bind one mission",
        )
        require(
            len(self.public_key) == 64 and all(c in "0123456789abcdef" for c in self.public_key),
            "SIGNATURE",
            "canonical Ed25519 public key required",
        )
        integer(self.activated_height)
        if self.retired_height is not None:
            integer(self.retired_height, low=self.activated_height + 1)
        require(type(self.revoked) is bool, "SHAPE", "boolean revocation required")

    def usable_at(self, height: int) -> bool:
        return (
            not self.revoked
            and self.activated_height <= height
            and (self.retired_height is None or height < self.retired_height)
        )


@dataclass(frozen=True)
class Verified:
    """Authenticated principals only; command semantics still require a transition."""

    command_digest: str
    actor: Credential
    signers: tuple[Credential, ...]
    height: int
    epoch: int

    def require_administration(self) -> None:
        require(self.actor.role == "administrator", "AUTHORITY", "administrative actor required")
        organizations = {
            credential.organization
            for credential in self.signers
            if credential.role == "administrator"
        }
        require(len(organizations) >= 3, "QUORUM", "three administrative organizations required")

    def require_role(self, role: str, mission: str) -> None:
        require(role in ROLES and role != "administrator", "AUTHORITY", "worker purpose required")
        require(self.actor.role == role, "AUTHORITY", "actor has a different signing purpose")
        require(self.actor.mission == mission, "SCOPE", "actor is outside mission")
