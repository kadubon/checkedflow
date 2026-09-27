"""Fetch one pinned Linux validation tool; never install or start a monitoring server."""

import argparse
import hashlib
import shutil
import tarfile
import time
import urllib.request
from pathlib import Path

from fetch_seaweedfs import ReleaseRedirect

VERSION = "3.15.0"
ARCHIVE_HASH = "2a542df32eac02ee17b9d844fb2aa1de00dafa5476579ba8a3ba862e9d572ea0"
BINARY_HASH = "c736d55d3ccd959fe48329965fb5cb671d45585a9483954766448035100ff75c"


def fetch(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    name = f"prometheus-{VERSION}.linux-amd64.tar.gz"
    archive = directory / name
    if not archive.exists():
        pending = directory / (name + ".partial")
        opener = urllib.request.build_opener(ReleaseRedirect, urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 120
        try:
            with (
                opener.open(
                    f"https://github.com/prometheus/prometheus/releases/download/v{VERSION}/{name}",
                    timeout=20,
                ) as response,
                pending.open("wb") as output,
            ):
                total = 0
                while part := response.read(1048576):
                    total += len(part)
                    if total > 268435456 or time.monotonic() > deadline:
                        raise SystemExit("Promtool download exceeded its byte/time limit")
                    output.write(part)
            pending.replace(archive)
        finally:
            pending.unlink(missing_ok=True)
    if archive.stat().st_size > 268435456:
        raise SystemExit("Promtool archive exceeds its byte limit")
    with archive.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != ARCHIVE_HASH:
            raise SystemExit("Promtool archive checksum mismatch")
    target = directory / "promtool"
    with tarfile.open(archive) as bundle:
        member = bundle.getmember(f"prometheus-{VERSION}.linux-amd64/promtool")
        if not member.isfile() or member.size > 268435456:
            raise SystemExit("Invalid promtool archive member")
        source = bundle.extractfile(member)
        if source is None:
            raise SystemExit("Promtool member missing")
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1048576)
    with target.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != BINARY_HASH:
            raise SystemExit("Promtool binary checksum mismatch")
    target.chmod(0o700)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    print(fetch(parser.parse_args().directory))
