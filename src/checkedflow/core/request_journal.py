"""Bounded v2 idempotency accounting; callers must establish command authority first.

This pure component neither dispatches work nor proves that archived bytes are durable.
Rollover returns the exact archive batch that a transactional store must retain.
"""

from dataclasses import dataclass, replace
from hashlib import sha256

from checkedflow.core.values import MAX_INT, Failure, integer, require

ARCHIVE_DOMAIN = b"CheckedFlow/request-archive/v2\x00"


def _identity(value: str) -> bytes:
    require(isinstance(value, str), "SHAPE", "identity must be a string")
    try:
        raw = value.encode("utf-8")
    except UnicodeError as exc:
        raise Failure("UNICODE", "invalid identity Unicode") from exc
    require(0 < len(raw) <= 80, "LIMIT", "identity must contain 1..80 UTF-8 bytes")
    return raw


def _hash(value: str) -> bytes:
    require(
        len(value) == 64 and all(c in "0123456789abcdef" for c in value),
        "BINDING",
        "canonical SHA-256 digest required",
    )
    return bytes.fromhex(value)


def _framed(value: str) -> bytes:
    raw = _identity(value)
    return len(raw).to_bytes(4, "big") + raw


@dataclass(frozen=True)
class Receipt:
    request: str
    actor: str
    nonce: int
    command_digest: str
    administrative: bool

    def encoded(self) -> bytes:
        integer(self.nonce, low=1)
        require(type(self.administrative) is bool, "SHAPE", "boolean admission class required")
        return (
            _framed(self.request)
            + _framed(self.actor)
            + self.nonce.to_bytes(8, "big")
            + _hash(self.command_digest)
            + bytes([self.administrative])
        )


@dataclass(frozen=True)
class Limits:
    ordinary_count: int = 128
    administrative_count: int = 16
    ordinary_bytes: int = 24576
    administrative_bytes: int = 4096

    def __post_init__(self) -> None:
        integer(self.ordinary_count, low=1, high=4096)
        integer(self.administrative_count, low=1, high=64)
        integer(self.ordinary_bytes, low=64, high=1048576)
        # One maximum-size receipt is 209 bytes. Reserve the entire control count.
        integer(self.administrative_bytes, low=209 * self.administrative_count, high=16384)


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class Journal:
    """Fixed actor slots and bounded active-epoch receipts; no whole-history dictionary."""

    actors: tuple[tuple[str, int], ...]
    limits: Limits = DEFAULT_LIMITS
    epoch: int = 0
    archive_root: str = "0" * 64
    receipts: tuple[Receipt, ...] = ()


def genesis(actors: tuple[str, ...], limits: Limits = DEFAULT_LIMITS) -> Journal:
    require(0 < len(actors) <= 64 and len(set(actors)) == len(actors), "LIMIT", "actor slots")
    for actor in actors:
        _identity(actor)
    return Journal(tuple((actor, 0) for actor in sorted(actors)), limits)


def _check(journal: Journal, receipt: Receipt) -> bool:
    receipt.encoded()
    prefix, separator, suffix = receipt.request.partition(":")
    require(
        separator == ":" and bool(suffix) and prefix.isascii() and prefix.isdecimal(),
        "EPOCH",
        "epoch prefix and nonempty request suffix required",
    )
    request_epoch = int(prefix)
    integer(request_epoch)
    require(str(request_epoch) == prefix, "EPOCH", "canonical epoch prefix required")
    require(request_epoch >= journal.epoch, "RETIRED_REQUEST", "request identity retired")
    require(request_epoch == journal.epoch, "EPOCH", "future request identity")
    for previous in journal.receipts:
        if previous.request == receipt.request:
            require(previous == receipt, "CONFLICT", "request identity has different command")
            return True
    nonces = dict(journal.actors)
    require(receipt.actor in nonces, "AUTHORITY", "unregistered actor slot")
    require(receipt.nonce == nonces[receipt.actor] + 1, "NONCE", "actor nonce must advance once")
    return False


def _advance_actor(journal: Journal, receipt: Receipt) -> tuple[tuple[str, int], ...]:
    return tuple(
        (actor, receipt.nonce if actor == receipt.actor else nonce)
        for actor, nonce in journal.actors
    )


def admit(journal: Journal, receipt: Receipt) -> tuple[Journal, bool]:
    """Record an already-authorized command; the Boolean result is duplicate acknowledgment."""
    if _check(journal, receipt):
        return journal, True
    ordinary = tuple(entry for entry in journal.receipts if not entry.administrative)
    administrative = tuple(entry for entry in journal.receipts if entry.administrative)
    current = administrative if receipt.administrative else ordinary
    count = (
        journal.limits.administrative_count
        if receipt.administrative
        else journal.limits.ordinary_count
    )
    byte_limit = (
        journal.limits.administrative_bytes
        if receipt.administrative
        else journal.limits.ordinary_bytes
    )
    require(len(current) < count, "CAPACITY", "request class count exhausted")
    size = sum(len(entry.encoded()) for entry in current) + len(receipt.encoded())
    require(size <= byte_limit, "CAPACITY", "request class byte ceiling exhausted")
    return replace(
        journal,
        actors=_advance_actor(journal, receipt),
        receipts=(*journal.receipts, receipt),
    ), False


@dataclass(frozen=True)
class Archive:
    previous_root: str
    epoch: int
    receipts: tuple[Receipt, ...]

    @property
    def root(self) -> str:
        integer(self.epoch)
        require(len(self.receipts) <= 4161, "LIMIT", "archive batch ceiling")
        hasher = sha256(ARCHIVE_DOMAIN)
        hasher.update(_hash(self.previous_root))
        hasher.update(self.epoch.to_bytes(8, "big"))
        hasher.update(len(self.receipts).to_bytes(4, "big"))
        for receipt in self.receipts:
            encoded = receipt.encoded()
            hasher.update(len(encoded).to_bytes(4, "big"))
            hasher.update(encoded)
        return hasher.hexdigest()


def rollover(journal: Journal, command: Receipt) -> tuple[Journal, Archive]:
    """A separately reserved checkpoint slot works even when both ordinary classes are full.

    The enclosing governance transition must verify three organizations, archive availability,
    active-obligation carry-forward and atomic persistence before committing this result.
    """
    require(command.administrative, "AUTHORITY", "rollover requires administrative admission")
    require(not _check(journal, command), "CONFLICT", "rollover requires a fresh request identity")
    require(journal.epoch < MAX_INT, "LIMIT", "epoch range exhausted")
    archive = Archive(journal.archive_root, journal.epoch, (*journal.receipts, command))
    return replace(
        journal,
        actors=_advance_actor(journal, command),
        epoch=journal.epoch + 1,
        archive_root=archive.root,
        receipts=(),
    ), archive
