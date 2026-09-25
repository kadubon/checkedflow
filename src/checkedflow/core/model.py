"""Finite records. Numeric quantities are integer protocol budget units."""

from dataclasses import dataclass, field

from checkedflow.core.values import Object, require


@dataclass(frozen=True)
class BlockHeight:
    """Committed time advancement, including a block with no accepted commands."""

    height: int


@dataclass(frozen=True)
class Context:
    """Trusted boundary input, constructed by signature verification, never from payload flags."""

    height: int
    signers: frozenset[str]
    command_digest: str


@dataclass
class Worker:
    key: str
    organization: str
    roles: tuple[str, ...]
    revoked: bool = False


@dataclass
class Verifier:
    contract: str
    image: str
    argv: tuple[str, ...]
    exhaustive: bool


@dataclass
class Mission:
    workers: tuple[str, ...]
    budget: int
    verification_reserve: int
    verification_cost: int
    expires: int
    max_rounds: int
    max_candidates: int
    max_depth: int
    max_attempts: int
    verifier: str
    receiver: str
    contract: str
    image: str
    spent: int = 0
    reserved: int = 0
    candidates: int = 0
    uses: int = 0


@dataclass
class Task:
    mission: str
    phase: str
    spec: Object
    dependencies: tuple[str, ...]
    cost: int
    ttl: int
    effect: str
    organization: str = ""
    subject: str = ""
    status: str = "ready"
    owner: str = ""
    fence: int = 0
    deadline: int = 0
    attempts: int = 0
    funded: bool = False
    result: Object = field(default_factory=dict)


@dataclass
class Capability:
    mission: str
    source: str
    source_digest: str
    task: str
    dependencies: tuple[str, ...]
    round: int
    depth: int
    expires: int
    status: str = "candidate"
    behavior: str = ""
    votes: dict[str, Object] = field(default_factory=dict)
    origin: str = "generated"


@dataclass
class Residual:
    subject: str
    reason: str
    trigger: str
    status: str = "open"
    resolution: str = ""


@dataclass
class State:
    chain: str
    organizations: dict[str, str]
    height: int = 0
    workers: dict[str, Worker] = field(default_factory=dict)
    verifiers: dict[str, Verifier] = field(default_factory=dict)
    missions: dict[str, Mission] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    capabilities: dict[str, Capability] = field(default_factory=dict)
    residuals: dict[str, Residual] = field(default_factory=dict)
    processed: dict[str, str] = field(default_factory=dict)
    nonces: dict[str, int] = field(default_factory=dict)


def genesis(chain: str, organizations: dict[str, str]) -> State:
    require(0 < len(chain) <= 128, "GENESIS", "chain identity is required")
    require(len(organizations) == 4, "GENESIS", "exactly four organizations are required")
    require(len(set(organizations.values())) == 4, "GENESIS", "keys must be distinct")
    require(
        all(0 < len(name) <= 80 for name in organizations),
        "GENESIS",
        "organization names must be 1..80 characters",
    )
    require(
        all(
            len(k) == 64 and all(c in "0123456789abcdef" for c in k) for k in organizations.values()
        ),
        "GENESIS",
        "Ed25519 keys required",
    )
    return State(chain=chain, organizations=dict(organizations))
