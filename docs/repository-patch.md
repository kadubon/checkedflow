# Repository-patch domain implementation boundary

Status: **PARTIAL, SOURCE-TESTED ADMISSION PRIMITIVES ONLY**. The end-to-end domain is not yet
available through the worker, consensus, CLI, A2A or MCP. Do not interpret this document as a
qualified execution path or an authorization to run candidate code on a host.

The [domain module](../src/checkedflow/domains/repository_patch.py) introduces a bounded immutable
tree and a file-replacement encoding for `repository-patch/v1`. No git invocation, archive
extraction, mutable branch lookup or repository URL fetch occurs during this admission step.
This deliberately small encoding makes preimages and changed bytes unambiguous. A later
GitHub adapter may construct a normal Git tree/diff from these already checked bytes.

## What the target binds

The approved contract must bind repository owner/name, exact commit, base and result tree
digests, exact patch digest, an immutable prebuilt environment image, checker, lint configuration,
expected test inventory, named receiver, explicit allowed files and resource/height ceilings.
Using a pinned prebuilt image is the initial dependency-environment option. No candidate-driven
dependency installation is allowed. Effect permission defaults to false. Constructing a Python
`Contract` object does not establish administrative approval; v2 admission must authenticate it.

Trees contain at most 128 sorted regular files, 256 KiB per file and 2 MiB total. Tree identity is
SHA-256 of canonical JSON with version and ordered `path`, `size`, `sha256` records. Source files
are exact UTF-8 bytes; line endings are not normalized. Patches replace, add or delete complete
files with an exact preimage digest (null for additions). Null content means deletion; an empty
string is an empty file. The final tree must match its approved digest.

Paths use a restricted ASCII portable spelling. Absolute paths, dot components, backslashes,
drive/stream syntax, reserved Windows device names, case collisions, file/directory collisions,
binary content and oversized data are rejected. The base manifest excludes hidden metadata such
as `.git` and `.github`; it is not a lossless arbitrary-repository archive. Only explicitly listed
Python module files may change. Tests, conftest, startup customization and setup controls cannot
be authorized by this restricted contract. A broader control-file contract is outside this profile.

## Independent output comparison

The evaluator owns a nonempty ordered inventory of named cases and expected JSON outputs.
`check_outputs` accepts bounded strict JSON containing exactly that result inventory. Missing
cases, duplicate keys, numeric coercion, unknown fields and a self-reported success flag cannot
grant acceptance. The comparison uses canonical bytes, distinguishing true from 1. The check
must run outside the candidate process after isolated execution. Inputs are not secret tests.

These checks can establish only agreement on the declared cases. A malicious program could
hard-code expected results, and repository test reports may be forged from inside an importing
process. Reported pytest/lint results, independent bounded properties and general correctness
must therefore remain separate evidence categories. No novelty or universal equivalence claim
follows from a digest of test outputs.

## Remaining integration obligations

`GVisorRunner.run_tree` now accepts the bounded immutable `Tree` representation. It creates
an inaccessible temporary parent, then materializes a fresh source directory with Linux
no-follow, directory-relative operations. Files contain exact bytes and are read-only; directories
allow the sandbox's nonroot user to traverse the read-only bind mount. Existing output directories
are rejected. The parent and all same-UID host processes must be operator-controlled. This is
not a general archive extractor or a defense against a compromised host.

The runner uses the same pinned-image, no-network, nonroot gVisor profile and bounded cleanup
as flat-file execution. Missing infrastructure still fails closed. A reported process result
establishes only execution status; callers must authenticate the contract and compare outputs
outside the sandbox. The separate legacy `run` method continues to reject nested filenames.

Controlled acquisition, pinned checker and test inventory, evidence signatures, artifact storage
integration and the v2 task lifecycle are pending. The nested-tree infrastructure test is mandatory
in the qualification gate; source-only filesystem tests cannot establish sandbox isolation. Runtime reuse must bind the original repository, base, environment,
receiver and unexpired evidence; a different base requires a new approved target and fresh checks.
See the [0.2 implementation record](implementation-0.2.0.md) for release gates.
