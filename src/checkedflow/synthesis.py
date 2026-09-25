"""Bounded synthesis of new Python programs for a declared finite task family.

Search interprets a finite grammar. Generated Python is only executed by the sandbox adapter.
The grammar interpreter is not evidence that generated Python executed correctly.
"""

import ast
import itertools
from dataclasses import dataclass
from typing import cast

from checkedflow.core.values import JSON, Failure, Object, array, integer, obj, require, text
from checkedflow.wire import digest

PRIMITIVES = ("double", "increment", "nonnegative", "reverse", "sort", "sum")
CONTRACT = "integer-arrays/v1"


@dataclass(frozen=True)
class Program:
    source: str
    operations: tuple[str, ...]
    dependencies: tuple[str, ...]
    candidates_tried: int


@dataclass(frozen=True)
class LibraryEntry:
    identity: str
    operations: tuple[str, ...]
    source: str = ""


def domain() -> list[list[int]]:
    """Exactly 31 inputs: length 0..2 and each element -2..2."""
    return [list(xs) for size in range(3) for xs in itertools.product(range(-2, 3), repeat=size)]


def interpret(values: list[int], operations: tuple[str, ...]) -> list[int] | int:
    result = list(values)
    for index, operation in enumerate(operations):
        require(operation in PRIMITIVES, "GRAMMAR", "unknown primitive")
        if operation == "sum":
            require(index == len(operations) - 1, "GRAMMAR", "sum must be terminal")
            return sum(result)
        if operation == "double":
            result = [x * 2 for x in result]
        elif operation == "increment":
            result = [x + 1 for x in result]
        elif operation == "nonnegative":
            result = [x for x in result if x >= 0]
        elif operation == "reverse":
            result = list(reversed(result))
        elif operation == "sort":
            result = sorted(result)
    return result


def source_for(operations: tuple[str, ...]) -> str:
    statements = {
        "double": "result = [x * 2 for x in result]",
        "increment": "result = [x + 1 for x in result]",
        "nonnegative": "result = [x for x in result if x >= 0]",
        "reverse": "result = list(reversed(result))",
        "sort": "result = sorted(result)",
        "sum": "result = sum(result)",
    }
    require(all(op in statements for op in operations), "GRAMMAR", "unknown source operation")
    body = "def solve(values):\n    result = list(values)\n"
    body += "".join(f"    {statements[op]}\n" for op in operations)
    body += "    return result\n"
    # Parse and unparse to emit a concrete AST-derived source artifact.
    return ast.unparse(ast.parse(body)) + "\n"


def _source(tokens: tuple[str, ...], library: dict[str, LibraryEntry]) -> str:
    prelude = ""
    body = "def solve(values):\n    result = list(values)\n"
    for index, token in enumerate(tokens):
        if token in library:
            entry = library[token]
            name = f"reuse_{index}"
            tree = ast.parse(entry.source or source_for(entry.operations))
            require(
                all(isinstance(node, ast.FunctionDef) for node in tree.body),
                "GRAMMAR",
                "reusable source must contain only function definitions",
            )
            require(
                any(
                    isinstance(node, ast.FunctionDef) and node.name == "solve" for node in tree.body
                ),
                "GRAMMAR",
                "missing solve function",
            )
            # Nest the entire admitted module, preserving names across multiple reuse generations.
            nested = "def " + name + "(values):\n"
            nested += "\n".join("    " + line for line in ast.unparse(tree).splitlines())
            nested += "\n    return solve(values)\n"
            prelude += nested + "\n"
            body += f"    result = {name}(result)\n"
        else:
            tree = ast.parse(source_for((token,)))
            function = cast(ast.FunctionDef, tree.body[0])
            body += "    " + ast.unparse(function.body[1]) + "\n"
    body += "    return result\n"
    return ast.unparse(ast.parse(prelude + body)) + "\n"


def synthesize(
    target: tuple[str, ...],
    *,
    max_candidates: int = 256,
    max_depth: int = 3,
    library: tuple[LibraryEntry, ...] = (),
) -> Program:
    require(1 <= max_candidates <= 4096 and 1 <= max_depth <= 4, "LIMIT", "invalid search bound")
    require(len(library) <= 16, "LIMIT", "library bound")
    expected = [interpret(values, target) for values in domain()]
    entries = {f"use:{entry.identity}": entry for entry in library}
    require(len(entries) == len(library), "DUPLICATE", "duplicate library entry")
    alphabet = tuple(sorted(entries)) + PRIMITIVES
    attempts = 0
    for size in range(1, max_depth + 1):
        for tokens in itertools.product(alphabet, repeat=size):
            expanded = tuple(
                op
                for token in tokens
                for op in (entries[token].operations if token in entries else (token,))
            )
            if "sum" in expanded[:-1]:
                continue
            attempts += 1
            require(attempts <= max_candidates, "SEARCH_EXHAUSTED", "candidate budget exhausted")
            actual = [interpret(values, expanded) for values in domain()]
            if actual == expected:
                deps = tuple(dict.fromkeys(entries[t].identity for t in tokens if t in entries))
                return Program(_source(tokens, entries), expanded, deps, attempts)
    raise Failure("SEARCH_EXHAUSTED", "no program in bounded grammar")


def request(value: Object) -> Object:
    """Generator process contract: one JSON request produces one candidate JSON document."""
    require(
        set(value) == {"target", "max_candidates", "max_depth", "library"},
        "SHAPE",
        "generator fields",
    )
    target = tuple(text(v) for v in array(value["target"], limit=4))
    library: list[LibraryEntry] = []
    for raw in array(value["library"], limit=16):
        row = obj(raw)
        require(set(row) == {"id", "operations"}, "SHAPE", "library fields")
        library.append(
            LibraryEntry(text(row["id"]), tuple(text(v) for v in array(row["operations"], limit=4)))
        )
    program = synthesize(
        target,
        max_candidates=integer(value["max_candidates"], low=1, high=4096),
        max_depth=integer(value["max_depth"], low=1, high=4),
        library=tuple(library),
    )
    return {
        "source": program.source,
        "operations": list(program.operations),
        "dependencies": list(program.dependencies),
        "candidates_tried": program.candidates_tried,
    }


def behavior_digest(outputs: list[JSON]) -> str:
    return digest(
        {"contract": CONTRACT, "inputs": [list(xs) for xs in domain()], "outputs": outputs}
    )
