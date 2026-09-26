# Preparing an exact accepted patch for reuse

`checkedflow.repository_reuse.prepare` reconstructs an accepted repository patch from scoped
artifact storage. Every call reads the required objects again and checks their bytes. An old
acceptance record therefore cannot hide a missing object, corrupted evidence or a storage outage.

The result is an immutable in-memory tree plus the candidate, contract, committed-state hash,
height and object digests checked during that call. No candidate code executes and no files are
written to a checkout. This preparation result does not authorize a later execution or GitHub effect.

## Required inputs

The caller supplies its own validating node's committed v2 state, a candidate ID, the complete
**intended** repository contract, an `Inputs` record, an artifact-store adapter and a trusted local
`Access` policy result. Do not substitute an arbitrary remote API response for own-node validation.
Do not copy the accepted contract into the intended contract while ignoring a different actual
destination or base. Those intended-use bindings are the consumer's responsibility.

`Inputs` contains references for the base tree bundle, patch, test inventory and all distinct
retained observation objects. The artifact adapter authorizes reads; the preparation layer first
requires mission read permission, then checks each reference's scope, purpose, content type and
complete contract digest. It independently checks returned byte length and SHA-256, including for
a future provider that accidentally omits its own integrity check.

The base uses kind `source-tree`; the patch uses `patch`; test inventory and observation objects
use `evidence`. All use `application/json`. Their reference `manifest` is the complete contract
digest. An object's byte digest differs from the logical tree digest: the former identifies the
encoded bundle, while the latter identifies the ordered file manifest and exact file contents.

The [tree bundle schema](../src/checkedflow/data/repository-tree.schema.json) describes
`repository-tree/v1`: ordered path/content records containing UTF-8 regular files. The decoder also
enforces the domain's path, collision, file-count, byte and binary-content rules. No links, modes,
Git metadata, archive extraction or executable installation instructions are represented.
Encoded bundles are at most 4 MiB in addition to the domain's 2 MiB total file-content bound;
JSON escaping may make a large tree exceed the encoded limit.

## Checks before returning bytes

Preparation requires a structurally valid running state and an accepted candidate under the
current credential registry. It checks the complete desired contract digest, result-tree digest,
exclusive contract deadline and exact installed checker identity. It then:

1. Reads the base, patch and test inventory from authorized storage and verifies their bytes.
2. Reapplies the bounded patch in memory, checking the exact base, preimages and resulting tree.
3. Confirms the independently specified test inventory is nonempty and its entry module exists.
4. Requires exactly the retained observation-object inventory, with no duplicate or extra references.
5. Reads every observation again and checks its contract, tree, inventory, checker, image, attempt
   height, output digests and typed verdict against the signed committed record.

A numeric `1` cannot stand in for `true`. A signed pass over an object that reports failure or
unknown is rejected. The observation must have been made after that check started and within its
attempt window. An unknown fourth observation may coexist with three passes, but its object must
still be available and consistent. This conservative inventory policy can reduce availability;
it prevents silently dropping an inconvenient retained record.

The observation's stdout/stderr digests remain claims committed by that verifier. This function
does not reconstruct missing raw outputs or prove that the verifier honestly ran the checker.
Three organizational attestations remain a trust assumption. Shared checker defects and dishonest
signers are not eliminated by fetching their evidence bytes.

## Changed bases require fresh qualification

The full target includes repository/base identity, base and result trees, patch, image, checker,
test inventory, lint configuration, receiver, ceilings, deadline and external-effect policy. A change
in any field cannot inherit acceptance. There is no fuzzy application, cherry-pick, mutable branch
resolution or extraction of a generally reusable procedure.

For a new base, construct a new immutable contract, reserve new verification funding, admit four
new organizational checks and obtain new observations. The old candidate and its historical costs
remain. The new candidate is pending until its own quorum passes. A patch with identical bytes may
therefore be qualified twice under two distinct bases without claiming a new general capability.

The mandatory live changed-base test uses two content-distinct fixture trees and a reconstructible
Git commit object for the successor. It checks the object's identity with Git's read-only object
hasher, rejects inherited acceptance, performs four fresh gVisor invocations per target and checks
the separate costs. The bounded test uses two targets, eight checks, no model service, a fixed image
and the fixture's three-second sandbox ceiling per invocation. It runs on a disposable hosted VM
under the workflow's total timeout. See the [implementation record](implementation-0.2.0.md) for
the exact revision whose live test has actually passed.

## Example and operational boundary

```python
from checkedflow.repository_reuse import Inputs, prepare

inputs = Inputs(base_reference, patch_reference, inventory_reference, observation_references)
prepared = prepare(
    own_node.state(), candidate_id, intended_contract, inputs, artifact_store,
    access=operator_read_policy,
)
assert prepared.tree.digest == intended_contract.result_tree
```

The names in this example are operator-prepared inputs, not strings accepted as authorization
from an agent. `contract_digest`, `tree_bytes` and `decode_tree` are available for immutable
contract/bundle preparation. Store and sign the resulting exact bindings before work admission.

Availability is observed at read time and the state hash binds one snapshot. State may stop advancing,
evidence may be withdrawn or storage may fail immediately afterward. Before dispatch, a supervised
worker still needs fresh own-node state, a valid lease/fence and budget, and the approved sandbox.
It must inhibit work on stale consensus or uncertain cleanup. Those supervisor guarantees, replicated
availability receipts, retention pins, dependency propagation and full lifecycle archival remain
required integrations; this component does not supply them or declare the G1–G7 profile qualified.

Source tests cover scope changes, inconsistent signed evidence, forged provider bytes, loss and
corruption after acceptance, outage, authorization and tree-bundle rejection. Actual code execution
occurs only in the separate mandatory gVisor cases. Mock/source cases never qualify sandbox behavior.
