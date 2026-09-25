"""Strict JSON boundary and RFC 8785 canonical bytes."""

import json
from hashlib import sha256
from typing import cast

import rfc8785

from checkedflow.core.values import JSON, MAX_INT, Failure, Object, obj, require

MAX_BYTES = 16_777_216
MAX_TRANSACTION_BYTES = 1_048_576


def _pairs(pairs: list[tuple[str, JSON]]) -> Object:
    result: Object = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_KEY", "JSON object has duplicate keys")
        result[key] = value
    return result


def _no_float(value: str) -> JSON:
    raise Failure("NUMBER", f"floating point or non-finite token: {value[:32]}")


def validate(
    value: object, *, depth: int = 0, count: list[int] | None = None, string_limit: int = 262144
) -> JSON:
    if count is None:
        count = [0]
    count[0] += 1
    require(depth <= 32 and count[0] <= 2_000_000, "LIMIT", "JSON depth or node limit")
    if value is None or type(value) is bool:
        return cast(JSON, value)
    if type(value) is int:
        require(-MAX_INT <= value <= MAX_INT, "NUMBER", "integer outside interoperable range")
        return value
    if isinstance(value, str):
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError as exc:
            raise Failure("UNICODE", "unpaired surrogate") from exc
        require(size <= string_limit, "LIMIT", "string byte limit")
        return value
    if isinstance(value, list):
        return [validate(v, depth=depth + 1, count=count, string_limit=string_limit) for v in value]
    if isinstance(value, dict):
        require(all(isinstance(k, str) for k in value), "SHAPE", "non-string object key")
        return {
            str(validate(k, depth=depth + 1, count=count, string_limit=string_limit)): validate(
                v, depth=depth + 1, count=count, string_limit=string_limit
            )
            for k, v in value.items()
        }
    raise Failure("SHAPE", "unsupported JSON value")


def loads(raw: bytes | str, *, string_limit: int = 262144) -> JSON:
    if isinstance(raw, str):
        try:
            raw = raw.encode("utf-8")
        except UnicodeError as exc:
            raise Failure("UNICODE", "invalid Unicode input") from exc
    require(len(raw) <= MAX_BYTES, "LIMIT", "document byte limit")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_no_float,
            parse_constant=_no_float,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, Failure):
            raise
        raise Failure("JSON", "malformed JSON") from exc
    return validate(value, string_limit=string_limit)


def dumps(value: JSON, *, string_limit: int = 262144) -> bytes:
    try:
        result = rfc8785.dumps(validate(value, string_limit=string_limit))
    except rfc8785.CanonicalizationError as exc:
        raise Failure("CANONICAL", "cannot canonicalize value") from exc
    require(len(result) <= MAX_BYTES, "LIMIT", "document byte limit")
    return result


def digest(value: JSON) -> str:
    return sha256(dumps(value)).hexdigest()


def document(raw: bytes | str, *, string_limit: int = 262144) -> Object:
    return obj(loads(raw, string_limit=string_limit))


def transaction_document(raw: bytes) -> Object:
    """Bound original transaction bytes before parsing or canonicalizing away whitespace."""
    require(len(raw) <= MAX_TRANSACTION_BYTES, "LIMIT", "transaction byte limit")
    return document(raw)
