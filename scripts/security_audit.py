"""Scan publishable source and distribution members for secrets and personal local paths."""

import argparse
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
TREES = ("src", "tests", "scripts", "docs", "deploy", ".agents", ".github")
ROOT_FILES = (
    "README.md",
    "skills.md",
    "AGENTS.md",
    "LICENSE",
    "NOTICE",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    ".gitignore",
    ".gitattributes",
    "SECURITY.md",
    "CHANGELOG.md",
)
PATTERNS = {
    "personal-local-path": re.compile(
        r"(?i)(?:[a-z]:[\\/](?:Users|Documents and Settings)[\\/][^\s/\\]+"
        r"|/(?:home|Users)/[a-z0-9_.-]+/|/mnt/[a-z]/Users/[^\s/]+/)"
    ),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "github-credential": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"
    ),
    "pypi-credential": re.compile(r"\bpypi-[A-Za-z0-9_-]{40,}\b"),
    "cloud-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}


def check(name: str, data: bytes) -> list[str]:
    path = PurePosixPath(name)
    errors = []
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.suffix in {".key", ".pem"}
        or any(
            part in {".env", ".tools", ".runtime", ".venv", "reports", "__pycache__"}
            for part in path.parts
        )
        or ".sqlite" in path.name
    ):
        errors.append(f"private or unsafe member: {name}")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return errors
    for label, pattern in PATTERNS.items():
        for match in pattern.finditer(content):
            # Report location and kind only, never a potentially secret matched value.
            line = content.count("\n", 0, match.start()) + 1
            errors.append(f"{label}: {name}:{line}")
    return errors


def inspect(dist: Path | None = None) -> tuple[int, list[str]]:
    errors, count = [], 0
    paths = [
        *(ROOT / name for name in ROOT_FILES if (ROOT / name).is_file()),
        *(
            p
            for name in TREES
            for p in (ROOT / name).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        ),
    ]
    for path in paths:
        errors.extend(check(path.relative_to(ROOT).as_posix(), path.read_bytes()))
        count += 1
    if dist:
        for path in sorted(dist.glob("*.whl")):
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    errors.extend(check(name, archive.read(name)))
                    count += 1
        for path in sorted(dist.glob("*.tar.gz")):
            with tarfile.open(path) as archive:
                for member in archive.getmembers():
                    if member.issym() or member.islnk():
                        errors.append(f"archive link: {member.name}")
                    if member.isfile():
                        stream = archive.extractfile(member)
                        if stream is not None:
                            errors.extend(check(member.name, stream.read()))
                            count += 1
    return count, errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path)
    count, errors = inspect(parser.parse_args().dist)
    print("\n".join(errors))
    print(f"Publication scan: {count} files/members, {len(errors)} findings")
    raise SystemExit(bool(errors))
