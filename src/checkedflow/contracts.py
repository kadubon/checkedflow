"""Packaged, offline schema admission; never follow input-controlled URLs."""

import json
from functools import lru_cache
from importlib.resources import files

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from checkedflow.core.values import Failure, Object


@lru_cache(maxsize=1)
def validator() -> Draft202012Validator:
    schema = json.loads(
        files("checkedflow").joinpath("data/envelope.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def check(envelope: Object) -> None:
    try:
        validator().validate(envelope)
    except ValidationError as exc:
        raise Failure("SCHEMA", "envelope does not satisfy checkedflow/v1") from exc
