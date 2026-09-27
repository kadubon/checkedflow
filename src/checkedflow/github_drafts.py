"""Privileged draft-PR provider for pre-staged, exactly bound Git commits.

Plans are not authorization. Only a separate authorized executor may own this adapter, its token
and its private journal. Byte-bound methods independently check complete source and patch trees;
consensus eligibility, staging writes and supervisor freshness remain separate integrations.
No branch writes, merging, closing or automatic POST retries are provided.
"""

import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from checkedflow.core.values import (
    JSON,
    Failure,
    Object,
    array,
    fields,
    integer,
    obj,
    require,
    text,
)
from checkedflow.domains.repository_patch import Contract, Tree, apply_patch
from checkedflow.git_tree import tree_id
from checkedflow.wire import document, dumps, loads, validate

API_VERSION = "2026-03-10"
MAX_RESPONSE = 1_048_576


@dataclass(frozen=True)
class Token:
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        require(
            isinstance(self.value, str)
            and 1 <= len(self.value) <= 4096
            and all(33 <= ord(c) <= 126 for c in self.value),
            "CREDENTIAL",
            "explicit printable GitHub credential required",
        )


@dataclass(frozen=True)
class Plan:
    repository: str
    repository_id: int
    operation: str
    authorization: str
    patch: str
    result: str
    base_branch: str
    base_commit: str
    head_commit: str
    git_tree: str

    def __post_init__(self) -> None:
        for digest in (self.operation, self.authorization, self.patch, self.result):
            require(
                re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                "BINDING",
                "SHA-256 binding required",
            )
        require(
            re.fullmatch(r"[A-Za-z0-9_-]{1,39}/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}", self.repository)
            is not None,
            "SCOPE",
            "fixed plan repository required",
        )
        integer(self.repository_id, low=1)
        for digest in (self.base_commit, self.head_commit, self.git_tree):
            require(
                re.fullmatch(r"[0-9a-f]{40}", digest) is not None,
                "BINDING",
                "Git SHA-1 object required",
            )
        require(
            self.base_commit != self.head_commit, "BINDING", "distinct approved commits required"
        )
        require(
            re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.base_branch) is not None,
            "SCOPE",
            "simple fixed base branch required",
        )

    @property
    def branch(self) -> str:
        return "checkedflow-effect/" + self.operation

    @property
    def title(self) -> str:
        return "CheckedFlow checked patch " + self.operation[:12]

    @property
    def body(self) -> str:
        return "CheckedFlow draft operation. Approval and verification are separate.\n\n" + dumps(
            self.record()
        ).decode("utf-8")

    def record(self) -> Object:
        return {"version": "checkedflow/github-draft-plan/v1", **obj(validate(asdict(self)))}


@dataclass(frozen=True)
class Outcome:
    status: str
    number: int = 0


def decode_plan(raw: bytes) -> Plan:
    """Parse intent metadata, never execution authority."""
    require(len(raw) <= 8192, "LIMIT", "draft plan byte ceiling")
    value = document(raw)
    fields(
        value,
        "version repository repository_id operation authorization patch result "
        "base_branch base_commit head_commit git_tree",
    )
    require(value["version"] == "checkedflow/github-draft-plan/v1", "VERSION", "draft plan profile")
    return Plan(
        text(value["repository"]),
        integer(value["repository_id"], low=1),
        *(
            text(value[name])
            for name in (
                "operation",
                "authorization",
                "patch",
                "result",
                "base_branch",
                "base_commit",
                "head_commit",
                "git_tree",
            )
        ),
    )


class Drafts:
    """One journal per executor/provider namespace. Disabled unless explicitly enabled.

    Never recreate or roll back a lost journal to retry an unknown dispatch. An independently
    restored replica has no shared SQL lock. Exclusive executor ownership is required separately.
    """

    def __init__(
        self,
        repository: str,
        repository_id: int,
        actor: str,
        token: Token,
        journal: Path,
        *,
        enabled: bool = False,
        timeout: float = 10,
    ) -> None:
        require(
            re.fullmatch(r"[A-Za-z0-9_-]{1,39}/[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}", repository)
            is not None,
            "SCOPE",
            "fixed GitHub repository required",
        )
        integer(repository_id, low=1)
        require(
            re.fullmatch(r"[A-Za-z0-9_\[\]-]{1,80}", actor) is not None,
            "SCOPE",
            "fixed provider actor",
        )
        require(type(enabled) is bool, "SHAPE", "explicit enable flag required")
        require(type(timeout) in {int, float} and 0 < timeout <= 30, "LIMIT", "bounded timeout")
        self.repository, self.repository_id, self.actor = repository, repository_id, actor
        self.token, self.journal, self.enabled, self.timeout = token, journal, enabled, timeout

    def _enabled(self, plan: Plan) -> None:
        require(self.enabled, "DISABLED", "GitHub effect provider disabled")
        self._scope(plan)

    def _scope(self, plan: Plan) -> None:
        require(
            plan.repository == self.repository and plan.repository_id == self.repository_id,
            "SCOPE",
            "plan belongs to another destination",
        )

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.journal.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        db = sqlite3.connect(self.journal, timeout=10)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                db.execute("BEGIN IMMEDIATE")
                tables = {
                    row[0]
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                require(
                    not tables or tables == {"identity", "operations"},
                    "VERSION",
                    "effect journal profile",
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS identity (id INTEGER PRIMARY KEY CHECK(id=1), "
                    "profile TEXT NOT NULL, repository TEXT NOT NULL, "
                    "repository_id INTEGER NOT NULL, actor TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS operations (operation TEXT PRIMARY KEY, "
                    "plan BLOB NOT NULL, status TEXT NOT NULL, number INTEGER NOT NULL)"
                )
                prior = db.execute(
                    "SELECT profile,repository,repository_id,actor FROM identity WHERE id=1"
                ).fetchone()
                expected = (
                    "checkedflow/github-draft-journal/v1",
                    self.repository,
                    self.repository_id,
                    self.actor,
                )
                if prior is None:
                    require(not tables, "STORAGE", "missing journal identity")
                    db.execute("INSERT INTO identity VALUES(1,?,?,?,?)", expected)
                else:
                    require(
                        prior == expected,
                        "SCOPE",
                        "journal belongs to another executor destination",
                    )
                yield db
        finally:
            db.close()

    def _request(
        self,
        method: str,
        suffix: str,
        *,
        payload: Object | None = None,
        params: dict[str, str] | None = None,
    ) -> JSON:
        headers = {
            "Authorization": "Bearer " + self.token.value,
            "Accept": "application/vnd.github+json",
            "Accept-Encoding": "identity",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "CheckedFlow-draft-provider",
        }
        try:
            with (
                httpx.Client(
                    timeout=self.timeout, follow_redirects=False, trust_env=False
                ) as client,
                client.stream(
                    method,
                    "https://api.github.com/repos/" + self.repository + suffix,
                    headers=headers,
                    content=None if payload is None else dumps(payload),
                    params=params,
                ) as response,
            ):
                require(
                    response.status_code == (201 if method == "POST" else 200),
                    "OUTCOME_UNKNOWN",
                    "provider response did not confirm operation",
                )
                require(
                    response.headers.get("content-encoding", "identity") == "identity",
                    "OUTCOME_UNKNOWN",
                    "compressed provider response rejected",
                )
                require(
                    'rel="next"' not in response.headers.get("link", ""),
                    "OUTCOME_UNKNOWN",
                    "provider lookup exceeds bounded page",
                )
                body = bytearray()
                for chunk in response.iter_bytes(65_536):
                    body.extend(chunk)
                    require(len(body) <= MAX_RESPONSE, "LIMIT", "provider response ceiling")
                return loads(bytes(body))
        except httpx.HTTPError:
            raise Failure(
                "OUTCOME_UNKNOWN", "provider transport did not confirm operation"
            ) from None

    def _match(self, plan: Plan, value: JSON) -> int:
        row = obj(value)
        head, base = obj(row.get("head")), obj(row.get("base"))
        require(
            row.get("draft") is True
            and row.get("state") == "open"
            and row.get("title") == plan.title
            and row.get("body") == plan.body
            and obj(row.get("user")).get("login") == self.actor
            and head.get("ref") == plan.branch
            and head.get("sha") == plan.head_commit
            and base.get("ref") == plan.base_branch
            and base.get("sha") == plan.base_commit
            and integer(obj(head.get("repo")).get("id"), low=1) == self.repository_id
            and integer(obj(base.get("repo")).get("id"), low=1) == self.repository_id,
            "CONFLICT",
            "provider object differs from exact owned draft",
        )
        return integer(row.get("number"), low=1)

    def _existing(self, plan: Plan) -> int:
        rows = array(
            self._request(
                "GET",
                "/pulls",
                params={
                    "state": "all",
                    "head": self.repository.split("/")[0] + ":" + plan.branch,
                    "per_page": "2",
                },
            ),
            limit=2,
        )
        require(len(rows) <= 1, "CONFLICT", "multiple provider objects for operation")
        return self._match(plan, rows[0]) if rows else 0

    def _preflight(self, plan: Plan) -> None:
        repo = obj(self._request("GET", ""))
        require(
            integer(repo.get("id"), low=1) == self.repository_id
            and repo.get("full_name") == self.repository
            and repo.get("archived") is False,
            "SCOPE",
            "repository identity or state changed",
        )
        policy = obj(self._request("GET", "/actions/permissions"))
        require(
            policy.get("enabled") is False,
            "POLICY",
            "profile requires disabled destination Actions",
        )
        for branch, commit in (
            (plan.base_branch, plan.base_commit),
            (plan.branch, plan.head_commit),
        ):
            remote = obj(obj(self._request("GET", "/git/ref/heads/" + branch)).get("object"))
            require(
                remote.get("type") == "commit" and remote.get("sha") == commit,
                "CONFLICT",
                "branch moved away from approved commit",
            )
        remote_commit = obj(self._request("GET", "/git/commits/" + plan.head_commit))
        parents = array(remote_commit.get("parents"), limit=1)
        require(
            remote_commit.get("sha") == plan.head_commit
            and obj(remote_commit.get("tree")).get("sha") == plan.git_tree
            and len(parents) == 1
            and obj(parents[0]).get("sha") == plan.base_commit,
            "BINDING",
            "staged commit tree or parent differs",
        )

    @staticmethod
    def _prior(db: sqlite3.Connection, plan: Plan) -> Outcome | None:
        row = db.execute(
            "SELECT plan,status,number FROM operations WHERE operation=?", (plan.operation,)
        ).fetchone()
        if row is None:
            return None
        require(row[0] == dumps(plan.record()), "CONFLICT", "operation arguments changed")
        require(row[1] in {"unknown", "confirmed"}, "STORAGE", "invalid effect record")
        number = integer(row[2], low=1 if row[1] == "confirmed" else 0)
        require(
            row[1] == "confirmed" or number == 0, "STORAGE", "unknown record has object identity"
        )
        return Outcome(row[1], number)

    def _confirmed(self, plan: Plan, number: int) -> Outcome:
        with self._db() as db:
            prior = self._prior(db, plan)
            require(prior is not None, "STORAGE", "dispatch claim missing")
            require(
                prior is None or prior.number in {0, number}, "CONFLICT", "confirmed object changed"
            )
            db.execute(
                "UPDATE operations SET status='confirmed',number=? WHERE operation=?",
                (number, plan.operation),
            )
        return Outcome("confirmed", number)

    def dispatch(self, plan: Plan, *, before_send: Callable[[Plan], None] | None = None) -> Outcome:
        """At most one POST per retained claim; confirmations are historical.

        A supervisor may supply a final authority/freshness check. It runs after provider
        reads and the durable claim, immediately before a new POST. Any raised exception
        prevents sending and retains the claim; it never grants a retry. This local check
        cannot atomically lock remote consensus or cancel a request already in flight.
        """
        self._enabled(plan)
        with self._db() as db:
            prior = self._prior(db, plan)
            if prior is not None:
                return prior
        self._preflight(plan)
        existing = self._existing(plan)
        with self._db() as db:
            prior = self._prior(db, plan)
            if prior is not None:
                return prior
            db.execute(
                "INSERT INTO operations VALUES(?,?,'unknown',0)",
                (plan.operation, dumps(plan.record())),
            )
        # The claim commits before the POST. A crash here is deliberately indistinguishable from
        # a lost successful reply. Only read reconciliation can establish a matching remote object.
        try:
            if not existing:
                if before_send is not None:
                    before_send(plan)
                self._enabled(plan)
            number = existing or self._match(
                plan,
                self._request(
                    "POST",
                    "/pulls",
                    payload={
                        "title": plan.title,
                        "body": plan.body,
                        "head": plan.branch,
                        "base": plan.base_branch,
                        "draft": True,
                        "maintainer_can_modify": False,
                    },
                ),
            )
            return self._confirmed(plan, number)
        except (Failure, OSError, sqlite3.Error):
            return Outcome("unknown")

    def _patch_binding(self, plan: Plan, base: Tree, patch: bytes, contract: Contract) -> None:
        self._scope(plan)
        require(contract.allow_draft_pr, "AUTHORITY", "contract excludes draft effects")
        require(
            plan.repository == contract.repository
            and plan.base_commit == contract.base_commit
            and plan.patch == contract.patch_digest
            and plan.result == contract.result_tree,
            "BINDING",
            "draft plan differs from patch contract",
        )
        result = apply_patch(base, patch, contract)
        require(tree_id(result) == plan.git_tree, "BINDING", "staged tree differs from patch bytes")
        remote = obj(self._request("GET", "/git/commits/" + plan.base_commit))
        require(
            remote.get("sha") == plan.base_commit
            and obj(remote.get("tree")).get("sha") == tree_id(base),
            "BINDING",
            "base commit differs from complete source bytes",
        )

    def dispatch_patch(
        self,
        plan: Plan,
        base: Tree,
        patch: bytes,
        contract: Contract,
        *,
        before_send: Callable[[Plan], None] | None = None,
    ) -> Outcome:
        """Check complete source/patch bindings before dispatch, including retained receipts.

        Contract construction is not consensus authority. The caller must still enforce
        current acceptance, action authorization, fencing and supervised deadlines.
        """
        self._enabled(plan)
        self._patch_binding(plan, base, patch, contract)
        return self.dispatch(plan, before_send=before_send)

    def reconcile_patch(self, plan: Plan, base: Tree, patch: bytes, contract: Contract) -> Outcome:
        """Recheck byte bindings, then perform read-only reconciliation; never resend."""
        self._enabled(plan)
        self._patch_binding(plan, base, patch, contract)
        return self.reconcile(plan)

    def inspect_patch(self, plan: Plan, base: Tree, patch: bytes, contract: Contract) -> Outcome:
        """Read exact remote identity without enabling dispatch or changing the local journal.

        This bounded probe is allowed during local disable. It sends only GET requests and
        needs no retained provider claim. Missing, moved or mismatched objects remain unknown;
        even a positive observation grants neither a retry nor administrative reconciliation.
        """
        self._scope(plan)
        try:
            self._patch_binding(plan, base, patch, contract)
            self._preflight(plan)
            number = self._existing(plan)
            return Outcome("confirmed", number) if number else Outcome("unknown")
        except (Failure, OSError):
            return Outcome("unknown")

    def reconcile(self, plan: Plan) -> Outcome:
        """Read the original operation; even empty provider results never authorize resending."""
        self._enabled(plan)
        with self._db() as db:
            require(self._prior(db, plan) is not None, "NOT_FOUND", "operation not claimed")
        try:
            self._preflight(plan)
            number = self._existing(plan)
            return self._confirmed(plan, number) if number else Outcome("unknown")
        except (Failure, OSError, sqlite3.Error):
            return Outcome("unknown")
