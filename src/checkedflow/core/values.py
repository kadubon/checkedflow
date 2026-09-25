"""Bounded, language-neutral values and stable failures."""

from typing import NoReturn, cast

type JSON = None | bool | int | str | list["JSON"] | dict[str, "JSON"]
type Object = dict[str, JSON]
MAX_INT = 9_007_199_254_740_991


class Failure(ValueError):
    """A stable protocol failure. No rejected transition changes its input state."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def fail(code: str, message: str) -> NoReturn:
    raise Failure(code, message)


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        fail(code, message)


def obj(value: JSON) -> Object:
    require(isinstance(value, dict), "SHAPE", "expected an object")
    return cast(Object, value)


def text(value: JSON, *, limit: int = 256) -> str:
    require(isinstance(value, str) and 0 < len(value) <= limit, "SHAPE", "invalid string")
    return cast(str, value)


def integer(value: JSON, *, low: int = 0, high: int = MAX_INT) -> int:
    require(type(value) is int and low <= value <= high, "SHAPE", "invalid integer")
    return cast(int, value)


def array(value: JSON, *, limit: int = 1024) -> list[JSON]:
    require(isinstance(value, list) and len(value) <= limit, "SHAPE", "invalid array")
    return cast(list[JSON], value)


def names(value: JSON, *, limit: int = 1024) -> tuple[str, ...]:
    result = tuple(text(item) for item in array(value, limit=limit))
    require(len(set(result)) == len(result), "DUPLICATE", "duplicate identity")
    return result


def fields(value: Object, expected: str) -> None:
    require(set(value) == set(expected.split()), "SHAPE", "unexpected or missing fields")
