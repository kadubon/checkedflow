"""Governed application-key revisions; historical records remain explicit and bounded."""

from dataclasses import replace

from checkedflow.core.authority import Credential, Verified
from checkedflow.core.values import Object, fields, integer, require, text

MAX_REVISIONS = 1024
MAX_ACTIVATION_DELAY = 10_000


def roots(credentials: tuple[Credential, ...]) -> tuple[Credential, ...]:
    """Recover immutable identity ownership; withdrawal/rotation never changes purpose."""
    return tuple(
        replace(item, retired_height=None, revoked=False)
        for item in credentials
        if item.revision == 1
    )


def validate(credentials: tuple[Credential, ...], height: int) -> None:
    require(len(credentials) <= MAX_REVISIONS, "CAPACITY", "key revision capacity")
    require(
        tuple(sorted(credentials, key=lambda item: (item.identity, item.revision))) == credentials,
        "STATE",
        "credential order differs",
    )
    require(
        len({item.public_key for item in credentials}) == len(credentials),
        "SIGNATURE",
        "key reuse prohibited",
    )
    prior: dict[str, Credential] = {}
    for item in credentials:
        old = prior.get(item.identity)
        if old is None:
            require(item.revision == 1 and item.activated_height == 0, "STATE", "identity root")
        else:
            require(
                item.revision == old.revision + 1
                and item.organization == old.organization
                and item.role == old.role
                and item.mission == old.mission
                and old.retired_height == item.activated_height
                and old.activated_height <= height,
                "STATE",
                "invalid key revision lineage",
            )
        prior[item.identity] = item


def change(
    credentials: tuple[Credential, ...], kind: str, payload: Object, context: Verified
) -> tuple[Credential, ...]:
    """Caller already established current administration and the enclosing mission."""
    identity = text(payload.get("identity"), limit=80)
    revision = integer(payload.get("revision"), low=1)
    history = tuple(item for item in credentials if item.identity == identity)
    require(bool(history), "AUTHORITY", "unknown identity slot")
    if kind == "key.schedule":
        fields(payload, "mission identity revision public_key activation_height proof")
        old = history[-1]
        require(len(credentials) < MAX_REVISIONS, "CAPACITY", "key revision capacity")
        require(old.activated_height <= context.height, "STATE", "pending revision exists")
        require(revision == old.revision + 1, "REVISION", "next key revision required")
        public_key = text(payload["public_key"])
        require(context.possession_key == public_key, "SIGNATURE", "new-key possession required")
        require(
            public_key not in {item.public_key for item in credentials},
            "SIGNATURE",
            "key reuse prohibited",
        )
        activation = integer(
            payload["activation_height"],
            low=context.height + 1,
            high=context.height + MAX_ACTIVATION_DELAY,
        )
        successor = replace(
            old,
            revision=revision,
            public_key=public_key,
            activated_height=activation,
            retired_height=None,
            revoked=False,
        )
        updated = tuple(
            replace(item, retired_height=activation) if item == old else item
            for item in credentials
        ) + (successor,)
    else:
        require(kind == "key.revoke", "VERSION", "key command")
        fields(payload, "mission identity revision reason")
        require(text(payload["reason"]) in {"compromise", "lost"}, "SHAPE", "revocation reason")
        require(any(item.revision == revision for item in history), "REVISION", "unknown revision")
        updated = tuple(
            replace(item, revoked=True)
            if item.identity == identity and item.revision == revision
            else item
            for item in credentials
        )
    result = tuple(sorted(updated, key=lambda item: (item.identity, item.revision)))
    validate(result, context.height)
    return result
