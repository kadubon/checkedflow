"""Verify manual release identity and hashes of already tested distribution files."""

import hashlib
import json
import os
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    if project["version"] != "0.2.0":
        raise SystemExit("only the owner-authorized experimental 0.2.0 release is permitted")
    if os.environ.get("GITHUB_REPOSITORY") != "kadubon/checkedflow":
        raise SystemExit("publisher repository mismatch")
    if os.environ.get("GITHUB_REF") != "refs/tags/v" + project["version"]:
        raise SystemExit("release tag differs from package version")
    dist = root / "dist"
    manifest = json.loads((dist / "SHA256SUMS.json").read_text())
    actual = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in dist.iterdir()
        if path.suffix == ".whl" or path.name.endswith(".tar.gz")
    }
    if manifest != actual or len(actual) != 2:
        raise SystemExit("tested distribution hashes differ or files are missing")
    for name in actual:
        path = dist / name
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                (metadata,) = [
                    entry for entry in archive.namelist() if entry.endswith(".dist-info/METADATA")
                ]
                record = BytesParser().parsebytes(archive.read(metadata))
        else:
            with tarfile.open(path) as archive:
                (member,) = [
                    entry
                    for entry in archive.getmembers()
                    if entry.name.count("/") == 1 and entry.name.endswith("/PKG-INFO")
                ]
                stream = archive.extractfile(member)
                if stream is None:
                    raise SystemExit("missing source metadata")
                record = BytesParser().parsebytes(stream.read())
        if record["Name"] != project["name"] or record["Version"] != project["version"]:
            raise SystemExit("artifact metadata mismatch")
    print("Release identity and tested artifact hashes match")


if __name__ == "__main__":
    main()
