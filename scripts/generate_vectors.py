"""Maintain reviewed wire examples and a signed finite lifecycle fixture."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from conftest import Harness  # noqa: E402

from checkedflow.serialization import encode  # noqa: E402
from checkedflow.wire import dumps  # noqa: E402


def main() -> None:
    harness = Harness()
    harness.checked()
    canonical = [
        {"input": '{"z":0,"a":1}', "canonical_hex": "7b2261223a312c227a223a307d"},
        {"input": "-0", "canonical_hex": "30"},
        {
            "input": '{"\\ue000":1,"\\ud800\\udc00":2}',
            "canonical_hex": "7b22f0908080223a322c22ee8080223a317d",
        },
    ]
    invalid = [
        {"input": raw, "error": code}
        for raw, code in [
            ('{"a":1,"a":2}', "DUPLICATE_KEY"),
            ("1.0", "NUMBER"),
            ("1e1", "NUMBER"),
            ("9007199254740992", "NUMBER"),
            ('"\\ud800"', "UNICODE"),
            ("{", "JSON"),
        ]
    ]
    vectors = {
        "version": "checkedflow/v1",
        "canonical": canonical,
        "invalid": invalid,
        "lifecycle": {
            "genesis": encode(harness.initial),
            "events": [{"envelope": e, "height": h} for e, h in harness.events],
            "final_hash": harness.runtime.state_hash,
        },
    }
    harness.runtime.tick(9000)
    vectors["lifecycle"]["empty_block_suffix"] = {
        "height": 9000,
        "final_hash": harness.runtime.state_hash,
    }
    (ROOT / "src/checkedflow/data/vectors.json").write_bytes(dumps(vectors) + b"\n")


if __name__ == "__main__":
    main()
