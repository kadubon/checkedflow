"""Portable artifact references. A digest identifies bytes; it does not authorize access."""

from dataclasses import dataclass

from checkedflow.core.values import Object, fields, integer, require, text

MAX_ARTIFACT_BYTES = 4_194_304
KINDS = frozenset({"source-tree", "patch", "evidence", "archive", "snapshot"})


@dataclass(frozen=True)
class Reference:
    algorithm: str
    digest: str
    length: int
    content_type: str
    kind: str
    scope: str
    manifest: str
    version: str = "checkedflow/artifact/v1"

    def __post_init__(self) -> None:
        require(self.version == "checkedflow/artifact/v1", "VERSION", "artifact reference version")
        require(self.algorithm == "sha256", "VERSION", "artifact digest algorithm")
        for value in (self.digest, self.manifest):
            require(
                isinstance(value, str)
                and len(value) == 64
                and all(c in "0123456789abcdef" for c in value),
                "BINDING",
                "canonical SHA-256 digest required",
            )
        integer(self.length, high=MAX_ARTIFACT_BYTES)
        require(self.kind in KINDS, "SHAPE", "artifact kind")
        text(self.scope, limit=80)
        require(
            all(c in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in self.scope),
            "SCOPE",
            "portable scope identifier required",
        )
        require(
            self.content_type in {"application/json", "application/octet-stream", "text/plain"},
            "SHAPE",
            "artifact content type",
        )

    def record(self) -> Object:
        return {
            "version": self.version,
            "algorithm": self.algorithm,
            "digest": self.digest,
            "length": self.length,
            "content_type": self.content_type,
            "kind": self.kind,
            "scope": self.scope,
            "manifest": self.manifest,
        }


def reference(value: Object) -> Reference:
    fields(value, "version algorithm digest length content_type kind scope manifest")
    return Reference(
        text(value["algorithm"]),
        text(value["digest"]),
        integer(value["length"]),
        text(value["content_type"]),
        text(value["kind"]),
        text(value["scope"]),
        text(value["manifest"]),
        text(value["version"]),
    )
