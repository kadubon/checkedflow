"""Fetch one pinned external test binary; never install or start a persistent service."""

import argparse
import hashlib
import json
import platform
import time
import urllib.request
import zipfile
from pathlib import Path

VERSION = "2.1.1"
PINS = {
    "windows": "e07a39059d7c7380d6dc776fb5bee2183cbc3344cf9387d4cf11309b83c0dce3",
    "linux": "8aa90f9cea46f541fc7baa3d0ec692fc06afde9a248cc1f2dcac46a567c6f56b",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    system = platform.system().lower()
    if system not in PINS or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise SystemExit("Pinned test binaries support Linux/Windows amd64 only")
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    name = f"vault_{VERSION}_{system}_amd64.zip"
    url = f"https://releases.hashicorp.com/vault/{VERSION}/{name}"
    archive = directory / name
    if not archive.exists():
        deadline = time.monotonic() + 120
        pending = directory / (name + ".partial")
        try:
            with urllib.request.urlopen(url, timeout=30) as response, pending.open("wb") as output:
                if response.url != url:
                    raise SystemExit("Unexpected binary download redirect")
                total = 0
                while chunk := response.read(1_048_576):
                    total += len(chunk)
                    if total > 268_435_456 or time.monotonic() > deadline:
                        raise SystemExit("Binary download exceeded 256 MiB / 120 second limit")
                    output.write(chunk)
            pending.replace(archive)
        finally:
            pending.unlink(missing_ok=True)
    if archive.stat().st_size > 268_435_456:
        raise SystemExit("Cached Vault archive exceeds 256 MiB bound")
    with archive.open("rb") as source:
        archive_hash = hashlib.file_digest(source, "sha256").hexdigest()
    if archive_hash != PINS[system]:
        raise SystemExit("Vault archive checksum mismatch")
    executable = "vault.exe" if system == "windows" else "vault"
    target = directory / executable
    with zipfile.ZipFile(archive) as bundle:
        info = bundle.getinfo(executable)
        if info.file_size > 1_073_741_824:
            raise SystemExit("Vault executable exceeds 1 GiB extraction bound")
        with bundle.open(info) as source, target.open("wb") as output:
            while chunk := source.read(1_048_576):
                output.write(chunk)
        (directory / "LICENSE-Vault").write_bytes(bundle.read("LICENSE.txt"))
    target.chmod(0o700)
    with target.open("rb") as source:
        binary_hash = hashlib.file_digest(source, "sha256").hexdigest()
    record = {
        "version": VERSION,
        "source": url,
        "archive_sha256": archive_hash,
        "binary_sha256": binary_hash,
        "binary_bytes": target.stat().st_size,
        "license": "BUSL-1.1",
        "platform": system + "_amd64",
    }
    (directory / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
