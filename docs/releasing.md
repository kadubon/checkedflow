# Distribution and Trusted Publishing

The package is `checkedflow` version `0.1.0`, Apache-2.0, Python 3.12+. A release requires explicit
maintainer authorization, passing checks on exact artifacts and a manual workflow dispatch.
The repository is [kadubon/checkedflow](https://github.com/kadubon/checkedflow); distribution is
through [PyPI](https://pypi.org/project/checkedflow/).

The Trusted Publisher must match exactly:

| Field | Value |
|---|---|
| PyPI project | `checkedflow` |
| Repository owner/name | `kadubon/checkedflow` |
| Workflow filename | `workflow.yml` |
| Workflow repository path | `.github/workflows/workflow.yml` |
| Environment | `pypi` |
| Authentication | OIDC, `id-token: write` only on the publication job |

See [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/) for
the publisher exchange. No API token is stored in the workflow. The first publication using a
pending publisher creates the PyPI project only after the authorized upload succeeds.

## Before an authorized release

1. Review the source and [validation status](validation-status.md). Run all static, property,
   fault, packaging and actual infrastructure gates. A missing Linux runner cannot qualify.
2. Use the workflow's disposable Ubuntu runner. [provision_ci.py](../scripts/provision_ci.py)
   verifies the gVisor archive and CometBFT source hashes from
   [runtime-lock.json](../deploy/runtime-lock.json), installs the exact Go module, checks its
   checksum and pulls the fixed Python image. It changes Docker only on that disposable VM.
3. Review the workflow's immutable action revisions when updating dependencies. Current action
   references are full commit hashes; tags in comments are explanatory only. The runtime lock
   and frozen Python lock are separate, independently checked inputs.
4. After user authorization, provision repository and protected `pypi` environment, matching the
   pending publisher. Confirm package metadata and reserve the version tag `v0.1.0` for this source.
5. Dispatch `workflow.yml` manually from that version tag with `publish=true`.

Ordinary main-branch push/pull-request checks do not publish. Qualification uses a fresh hosted
VM without operator keys or a persistent privileged host. The manual publication job depends on successful build, all six platform
checks and actual qualification from the same workflow run.

`uv build` produces a wheel and sdist once. The build job checks their contents and installation
outside the checkout and records SHA-256 digests. Matrix jobs download and install those files;
the qualification job installs the same wheel. The publication job downloads the original
artifact, checks tag, metadata and digests, then gives only the wheel and sdist to PyPA. It does
not rebuild. PyPI OIDC permission is absent from all other jobs.

The `pypi` environment should allow only version tags (`v*`). Keep write access limited to
release maintainers. Never put API tokens in Actions secrets for this publisher. Use the exact
workflow filename above: renaming it invalidates the PyPI publisher identity.

## Local build verification

```sh
uv sync --frozen --all-extras --group dev
uv build
uv run twine check dist/*.whl dist/*.tar.gz
uv run python scripts/package_check.py --python 3.12
uv run python scripts/package_check.py --python 3.13
uv run python scripts/package_check.py --python 3.14
uv run python scripts/security_audit.py --dist dist
```

The source archive contains tests, documentation, schemas and release tools. The wheel contains
the SDK, CLI, type marker, schemas, vectors, research registry, minimal example and notices.
`checkedflow[distributed]` adds RPC/protobuf dependencies; CometBFT and gVisor remain external
execution infrastructure. A source checkout is not needed for SDK, CLI or packaged contracts.

`checkedflow[agents]` adds the official A2A/MCP SDKs and the A2A HTTP server. The bundled agent
CLI also needs `distributed` for its own-node backend. `package_check.py` tests base installation
before adding both extras and testing protocol discovery for each distribution. The wheel includes
the agent profile, request schema and runnable official-client example. Required qualification
now includes the cross-protocol four-node case; an older seven-case report cannot pass its gate.

## Evidence retained for a release

Keep the source identity, wheel/sdist SHA-256 manifest, six platform reports and required Linux
qualification report with the release review. Local `reports/` and `dist/` are ignored build
outputs; package source, tests, docs and schemas are included through the explicit sdist manifest.
Private laboratory keys, databases, downloaded toolchains and virtual environments are excluded.

The qualification XML gate checks the complete required case list and rejects skipped, failed or
errored cases. It does not independently authenticate report freshness. The workflow establishes
freshness by running that gate and the exact installed wheel's tests in the same run that supplies
publication artifacts. Do not reuse a passing historical XML file as release authorization.

Any change to source or packaged documentation invalidates an older distribution hash. Build once
after the final edits, run installation checks on those files, and pass those exact files forward.
Local actionlint validates syntax, not upstream action provenance. The build also audits the
hashed runtime requirements against current vulnerability advisories; failures block publication.

## Publish and verify

After the reviewed commit passes main-branch checks, create the matching version tag and dispatch
`workflow.yml` from it with `publish=true`. Wait for the publication job, then verify PyPI's JSON
metadata and both distribution SHA-256 values against the workflow's `distributions` artifact.
Install `checkedflow==0.1.0` into a fresh environment using PyPI and run the CLI/SDK examples.
Create a GitHub release for the same tag, attaching that exact wheel, sdist and SHA-256 manifest;
include test scope and application limits in the notes. Do not rebuild release attachments locally.

If publication fails, inspect its identity and gate error. Do not bypass a failing qualification,
upload with a token, silently change the version, or overwrite an existing PyPI file. A tag must
continue to identify its original release source. Correct a not-yet-released tag only through an
explicitly reviewed correction; published versions require a new version.
