# Repository-patch domain implementation boundary

Status: **PARTIAL: admission, isolated execution and independent observation components**. The end-to-end domain is not yet
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

Controlled acquisition, reported repository lint/test execution, evidence signatures, artifact
storage integration and the v2 task lifecycle are pending. The nested-tree infrastructure test is mandatory
in the qualification gate; source-only filesystem tests cannot establish sandbox isolation. Runtime reuse must bind the original repository, base, environment,
receiver and unexpired evidence; a different base requires a new approved target and fresh checks.
See the [0.2 implementation record](implementation-0.2.0.md) for release gates.


## Contract-bound independent observations

`checkedflow.repository_execution.observe_patch(base, patch, contract, cases, height=height)`
is an SDK component for a target that the caller has already authorized. It checks the exact base,
patch, resulting tree, inventory bytes, checker identity and deadline before creating a runner.
It does not obtain consensus, reserve a budget, issue a lease or authorize a worker. The supplied
height must come from the caller's fresh trusted full node; supplying an arbitrary number is not
proof of freshness. Worker integration must enforce those obligations before this call.

The inventory is strict JSON with this shape:

```json
{
  "version": "repository-cases/v1",
  "path": "shop/invoice.py",
  "function": "invoice_total",
  "cases": [
    {
      "case": "empty-invoice",
      "input": {"lines": [], "shipping_cents": 0, "discount_cents": 0},
      "output": 0
    }
  ]
}
```

The [inventory schema](../src/checkedflow/data/repository-cases.schema.json) describes structure.
Runtime validation additionally checks portable paths, unique case identities, strict numeric
representation, JSON nesting and the 256 KiB aggregate byte limit. There must be 1–128 cases.
Each invocation calls one explicitly named public function with one JSON argument per case.
Returned values must be interoperable JSON: integer arithmetic should use integer units rather
than floating point. This is a bounded function interface, not an arbitrary pytest runner.

Only input records, the entry path and the function name go to the sandbox. The installed wrapper
starts Python in isolated mode, then loads the selected source under `/work`. Candidate imports
and all function calls occur inside gVisor. The trusted evaluator compares the resulting bounded
report with expected answers after the process ends. The candidate can inspect the request,
interfere with its own interpreter or fabricate a response. Passing therefore establishes agreement
on these declared cases, not that a particular internal implementation was used or that unseen
inputs are correct. No secret-test, novelty or general-equivalence claim is supported.

`CHECKER_DIGEST` identifies a canonical manifest containing the wrapper and the exact installed
source bytes for the observation adapter, domain comparator, wire implementation and value
validation. Changes to these bytes change the target, including changes between source checkouts
and built distributions. Operators must use the same qualified distribution when approving and
checking this digest. This manifest is not a substitute for the release's full dependency and image
provenance; the contract separately pins the execution image.

The returned frozen `Observation` includes a digest of every contract field, observed height,
result tree, inventory, checker, image and stdout/stderr digests. Its `case_match` is:

- `true` only when the sandbox reports completed execution and all ordered case outputs match;
- `false` when a structurally complete output report differs from expected answers;
- `null` for timeout, uncertain cleanup, nonzero exit, malformed reports or omitted cases.

The reason records the distinction. Missing infrastructure raises a failure rather than invoking a
host fallback. Observations are unsigned local records. They do not establish a quorum, registration,
reuse eligibility, external effect permission or successful linting. The lint contract field is retained
in the full contract digest, but this output-only component does not execute a lint command.

## Included practical fixture

The [invoice fixture](../src/checkedflow/data/invoice-fixture.json) is original Apache-2.0 material
and includes a full license file, two small Python modules, a seeded quantity-accounting bug,
the corrected file and four independently specified cases. Cases cover quantities, an empty invoice,
shipping, discounts and a zero floor. Amounts use integer cents. This is a software test fixture,
not a payment or accounting service.

The fixture includes a reconstructible Git commit object and its exact base identity. Tests rebuild
the Git blob/tree/commit identities from the bundled bytes without importing candidate modules.
SHA-1 here denotes Git's legacy object identifier, not the security digest: CheckedFlow separately
binds the base, patch and result with SHA-256. The fixture repository name is a local logical identity;
no remote repository is fetched or assumed to exist.

Mandatory real-sandbox tests exercise the corrected fixture, wrong results, invalid Python,
self-reported PASS, an empty result inventory and nontermination. Source tests use a substituted
runner only to inspect requests and failure handling; those tests cannot establish actual isolation.
The full domain still needs authenticated work admission, evidence quorum, registration, scoped
reuse and fresh checks for a new base before it can satisfy the end-to-end release gate.
