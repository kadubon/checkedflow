"""Operator-owned mission/client authorization, independent of transaction signatures."""

import time
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from checkedflow.core.values import Failure, Object, array, fields, integer, obj, require, text
from checkedflow.wire import digest, document

ROLE_CONTRACT = document(files("checkedflow").joinpath("data/access-roles.json").read_bytes())
ROLES = obj(ROLE_CONTRACT["roles"])
COMMAND_ROLES = {str(kind): role for role, kinds in ROLES.items() for kind in array(kinds)}


@dataclass(frozen=True)
class Principal:
    """Trusted transport identity; never construct this from request metadata or roles."""

    issuer: str
    client: str
    subject: str
    expires: int = 0

    def record(self) -> Object:
        return {
            "issuer": self.issuer,
            "client": self.client,
            "subject": self.subject,
            "expires": self.expires,
        }

    @property
    def identity(self) -> str:
        return digest({key: value for key, value in self.record().items() if key != "expires"})

    @classmethod
    def restore(cls, record: Object) -> "Principal":
        fields(record, "issuer client subject expires")
        return cls(
            text(record["issuer"], limit=2048),
            text(record["client"], limit=256),
            text(record["subject"], limit=256),
            integer(record["expires"]),
        )


LOCAL = Principal("checkedflow:local", "operator", "operator")


class Policy:
    """Reload a bounded protected file for every decision, including active streams.

    Grants are an additional transport restriction. They cannot mint signatures, change
    committed roles, make receipts final or hide replicated state from validators.
    """

    def __init__(self, path: Path, chain: str, mission: str) -> None:
        self.path, self.chain, self.mission = path, chain, mission
        self._load()

    def _load(self) -> Object:
        try:
            with self.path.open("rb") as source:
                raw = source.read(65537)
        except OSError as exc:
            raise ValueError("access policy unavailable") from exc
        require(len(raw) <= 65536, "ACCESS", "access policy size limit")
        value = document(raw)
        fields(value, "profile grants")
        require(value["profile"] == "checkedflow/access-policy/v1", "ACCESS", "policy version")
        seen: set[tuple[str, ...]] = set()
        for item in array(value["grants"], limit=128):
            grant = obj(item)
            fields(grant, "issuer client subject chain mission roles actors")
            binding = tuple(
                text(grant[key], limit=limit)
                for key, limit in (
                    ("issuer", 2048),
                    ("client", 256),
                    ("subject", 256),
                    ("chain", 128),
                    ("mission", 80),
                )
            )
            require(binding not in seen, "ACCESS", "duplicate policy binding")
            seen.add(binding)
            roles = array(grant["roles"], limit=6)
            actors = array(grant["actors"], limit=128)
            require(
                all(isinstance(role, str) and role in ROLES for role in roles),
                "ACCESS",
                "unknown role",
            )
            require(len(set(str(role) for role in roles)) == len(roles), "ACCESS", "duplicate role")
            for actor in actors:
                text(actor, limit=80)
            require(
                len(set(str(actor) for actor in actors)) == len(actors), "ACCESS", "duplicate actor"
            )
        return value

    def check(self, principal: Principal | None, role: str = "inspect", *, actor: str = "") -> str:
        if not isinstance(principal, Principal):
            raise Failure("ACCESS", "authenticated client required")
        require(
            not principal.expires or time.time() < principal.expires,
            "ACCESS",
            "client authorization expired",
        )
        try:
            policy = self._load()
        except (ValueError, OSError) as exc:
            raise Failure("ACCESS", "access policy unavailable or invalid") from exc
        for item in array(policy["grants"]):
            grant = obj(item)
            if all(
                grant[key] == expected
                for key, expected in (
                    ("issuer", principal.issuer),
                    ("client", principal.client),
                    ("subject", principal.subject),
                    ("chain", self.chain),
                    ("mission", self.mission),
                )
            ):
                require(role in array(grant["roles"]), "ACCESS", "client role denied")
                require(not actor or actor in array(grant["actors"]), "ACCESS", "actor denied")
                return digest(policy)
        raise Failure("ACCESS", "client mission denied")

    def command(self, principal: Principal | None, command: Object) -> None:
        require(command.get("api_version") == "checkedflow/v2", "ACCESS", "v2 policy required")
        require(
            command.get("chain") == self.chain
            and obj(command.get("payload")).get("mission") == self.mission,
            "ACCESS",
            "command scope denied",
        )
        kind = text(command.get("kind"))
        require(kind in COMMAND_ROLES, "ACCESS", "command role undefined")
        self.check(principal, COMMAND_ROLES[kind], actor=text(command.get("actor")))
