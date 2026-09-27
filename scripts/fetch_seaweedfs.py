"""Fetch a checksum-pinned external test service without starting or installing it."""

import argparse
import hashlib
import json
import platform
import tarfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

VERSION = "4.47"
COMMIT = "c5073360007d28385a33426a42ac3e4ec504c5a3"
PINS = {
    "windows": (
        "windows_amd64.zip",
        "8809359079e62fcd60574ff661449160899622c52072f3f569d346669079efe9",
    ),
    "linux": (
        "linux_amd64.tar.gz",
        "31fb804858885f9e7f18b6d3b1da09e824baac3e6a55b5a62c3c4c77e6ed6d7d",
    ),
}


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if (
            parsed.scheme != "https"
            or parsed.hostname
            not in {
                "github.com",
                "release-assets.githubusercontent.com",
                "objects.githubusercontent.com",
            }
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise SystemExit("Unexpected release download redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(directory: Path) -> Path:
    system = platform.system().lower()
    if system not in PINS or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("Pinned service binaries support Linux/Windows amd64 only")
    directory.mkdir(parents=True, exist_ok=True)
    name, expected = PINS[system]
    archive = directory / name
    url = f"https://github.com/seaweedfs/seaweedfs/releases/download/{VERSION}/{name}"
    if not archive.exists():
        pending = directory / (name + ".partial")
        deadline = time.monotonic() + 120
        opener = urllib.request.build_opener(ReleaseRedirect, urllib.request.ProxyHandler({}))
        try:
            with opener.open(url, timeout=20) as response, pending.open("wb") as output:
                total = 0
                while part := response.read(1048576):
                    total += len(part)
                    if total > 67108864 or time.monotonic() > deadline:
                        raise SystemExit("Service download exceeded 64 MiB / 120 second bound")
                    output.write(part)
            pending.replace(archive)
        finally:
            pending.unlink(missing_ok=True)
    if archive.stat().st_size > 67108864:
        raise SystemExit("Cached service archive exceeds limit")
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise SystemExit("Service archive checksum mismatch")
    executable = "weed.exe" if system == "windows" else "weed"
    target = directory / executable
    if system == "windows":
        with zipfile.ZipFile(archive) as bundle:
            info = bundle.getinfo(executable)
            if info.file_size > 268435456:
                raise SystemExit("Service executable exceeds 256 MiB bound")
            with bundle.open(info) as source, target.open("wb") as output:
                while part := source.read(1048576):
                    output.write(part)
    else:
        with tarfile.open(archive) as bundle:
            member = bundle.getmember(executable)
            if not member.isfile() or member.size > 268435456:
                raise SystemExit("Invalid service executable archive member")
            source = bundle.extractfile(member)
            if source is None:
                raise SystemExit("Missing executable bytes")
            with source, target.open("wb") as output:
                while part := source.read(1048576):
                    output.write(part)
    target.chmod(0o700)
    with target.open("rb") as stream:
        fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
    record = {
        "version": VERSION,
        "commit": COMMIT,
        "license": "Apache-2.0",
        "source": url,
        "archive_sha256": expected,
        "binary_sha256": fingerprint,
        "binary_bytes": target.stat().st_size,
        "platform": system + "_amd64",
    }
    (directory / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    fetch(parser.parse_args().directory.resolve())
