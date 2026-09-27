# GitHub draft provider: send once, reconcile by reading

Status: development provider component, disabled by default. It is not the complete 0.2 external
effect lifecycle. The published 0.1.0 distribution does not contain it.

## The problem

A client can lose the response after GitHub creates a pull request. Sending the request again
could create another observable action. A draft also does not neutralize destination CI: branch
pushes and PR creation can trigger other systems. These problems remain even when a patch has
passed its declared checks.

`checkedflow.github_drafts.Drafts` handles one deliberately narrow provider operation: open a
draft from an **already staged exact Git commit** in one fixed repository. Install development
builds with the `effects` extra to include HTTPX. The base SDK does not acquire that dependency.

The adapter does not stage patch bytes, create or update branches, merge, close PRs, execute code,
or interpret consensus approvals. The caller must be a separate trusted effect executor. Keep
its credential and private journal away from candidates, ordinary workers, verifiers and gateways.
Constructing or decoding a plan does not prove authority to send it.
The [v2 effect state machine](work-effects.md) now supplies governed reservations and a strict
intent-to-plan resolver. Complete operational deployment and compensation remain incomplete.
The [supervised dispatch SDK](effect-dispatch.md) connects current protected policy, scoped
evidence and own-node freshness to the final check. The [durable supervisor](effect-supervision.md)
adds ordinary signed reporting and local crash recovery; cross-host failover remains open.

## Exact destination and content checks

The constructor fixes the repository name, immutable numeric repository ID and expected PR actor.
The [plan schema](../src/checkedflow/data/github-draft-plan.schema.json) repeats that repository
name and numeric ID; a different configured destination rejects before I/O. It binds operation identity,
authorization digest, patch digest, result digest, base branch, base commit, staged head commit
and Git tree. The [vector](../src/checkedflow/data/github-draft-vector.json) is inert test metadata.
All four application digests are SHA-256. Git object IDs are the supported GitHub SHA-1 form;
these different representations are not interchangeable.

The head branch is always `checkedflow-effect/<operation-digest>`. Title and body are derived from
the exact plan; arbitrary user text cannot change request arguments. The initial profile supports
simple base branch names using letters, digits, `_` and `-`, up to 80 characters. The staged
commit must have exactly one parent, the approved base, and the exact declared Git tree.

Before sending, the adapter reads the repository identity, requires it to be unarchived, checks
that GitHub Actions is disabled, verifies both branch tips and reads the staged commit. It searches
all PR states for the owned head branch, including human-changed bases. An existing object must
match actor, both repository IDs, branches, commits, title, body, open state and draft status.
Multiple, closed, changed or foreign objects prevent a new POST.

Use `dispatch_patch(plan, base, patch_bytes, contract)` to independently reconstruct these bindings.
It checks the contract's explicit draft permission, repository, base commit, patch digest and result
digest, then applies the bounded patch in memory. `checkedflow.git_tree.tree_id` encodes every
file as a Git blob and every directory as a Git tree, preserving exact bytes. It compares the result
with the plan and reads the base commit to compare its tree with the complete supplied source.
Only then does the provider perform its normal dispatch checks. This extra binding also applies
when a retained receipt exists. `reconcile_patch` makes the same checks before read reconciliation.
Binding/read failures raise a typed failure without changing a retained receipt or sending a POST.

The source must represent the **whole repository**, within the repository-patch size limits.
Files use mode `100644`; directories use `40000`. Executable files, symlinks, submodules and omitted
files cannot silently survive the comparison. No checkout, filters, line-ending conversion or
candidate execution occurs. The encoding follows [Git object storage](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects)
and is tested against real `git hash-object` and `git mktree`. SHA-1 here is a Git compatibility
identifier, not a replacement for the contract's SHA-256 content identities or signatures.
Packaged [portability vectors](../src/checkedflow/data/git-tree-vectors.json) fix empty-file,
directory ordering, nested paths, Unicode content and CRLF byte behavior for other implementations.

The lower-level `dispatch(plan)` and `reconcile(plan)` remain metadata-only APIs for trusted
executors. Neither API establishes current consensus acceptance or execution authority, and
constructing a contract does not grant either. Runtime staging and complete effect integration
remain unfinished; these methods are not a complete approved-patch-to-PR workflow.

## Journal and unknown outcomes

One private SQLite journal belongs to one destination and actor. A transaction commits an
`unknown` dispatch claim before the POST. Competing calls against that journal cannot both claim
the same operation. A matching response records a `confirmed` receipt and PR number.

| Situation | Result |
|---|---|
| Provider disabled | Reject before filesystem or network access |
| Existing exact owned open draft | Record the observed object without creating another |
| Same operation and same plan repeated | Return its retained receipt; no POST |
| Same operation with different arguments | Reject |
| Crash before/after send, timeout, ambiguous response or failed receipt persistence | Preserve `unknown` |
| Explicit `reconcile(plan)` sees the exact original draft | Record confirmation without POST |
| Reconciliation sees no object, moved branches, changed content or unavailable provider | Remain unknown; do not infer permission to resend |

Confirmation is a historical observation, not a promise that the PR remains unchanged. A fresh
reconciliation can return unknown while retaining the earlier confirmed receipt. A different PR
number cannot overwrite a confirmed identity. The module makes no exactly-once remote claim.

Do not recreate, copy or roll back the journal to escape an unknown outcome. Independent journals
do not share a lock. Exclusive executor ownership and authenticated coordinated recovery must be
implemented before operational failover is supported. The journal keeps indexed operation history;
it is not automatically pruned and needs operator disk budgeting.

## Transport and credentials

### Final local dispatch check

Both `dispatch` and `dispatch_patch` accept the keyword `before_send`, a trusted
executor callback taking the exact `Plan`. It must return only when current authority,
policy, artifacts and own-node freshness permit that plan. Raise `Failure` to inhibit
sending. The callback runs after all provider reads and after the unknown claim has
committed, directly before a new POST. The provider rechecks its local enable flag after
the callback. Keep the callback bounded and outside candidate-controlled code.

This addresses a specific race: a supervisor may approve a plan, then lose quorum or
receive a stop while provider preflight performs network reads. Checking only at entry
would miss that change. A failed final check leaves a durable unknown claim and performs
no POST. Repeating the operation cannot bypass the failed check by omitting the callback.
Unexpected callback exceptions propagate while preserving that same claim. An existing
exact remote object or a retained receipt is a historical observation; neither invokes
the new-send callback or creates a second object.

The optional callback preserves the low-level provider API. Omitting it does not implement
operational supervision. This hook does not itself load protected policy, poll a validating
node, cancel an in-flight HTTP request, or make the local check atomic with remote consensus.
The maintained full executor and its G3 crash/recovery qualification remain required.

Requests go only to `https://api.github.com`, using the explicit token supplied by the executor.
The adapter does not discover ambient credentials, follow redirects, use environment proxies,
accept arbitrary endpoints or fetch URLs from responses. It pins REST API version `2026-03-10`,
bounds each response to 1 MiB, uses strict duplicate-key/integer JSON parsing and rejects compressed
responses or a lookup requiring another page. It sends one create request with `draft=true` and
`maintainer_can_modify=false`. Network errors do not include credentials in public error messages.

Use a repository-scoped GitHub App token or reviewed equivalent credential with only the necessary
permissions. Consult GitHub's current documentation for [PR creation](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request),
[Git commit reads](https://docs.github.com/en/rest/git/commits#get-a-commit) and
[Actions permission reads](https://docs.github.com/en/rest/actions/permissions#get-github-actions-permissions-for-a-repository).
The operator must review the complete destination CI/webhook policy. A point-in-time Actions check
cannot prevent a trusted administrator from changing settings between checks or stop external CI.
This initial component requires Actions disabled; it does not support enabling candidate CI.

HTTPX timeout is per I/O operation, bounded to at most 30 seconds. It is not a complete supervised
attempt deadline. The future executor must enforce total deadlines, own-node freshness, current
fences, emergency disable and current acceptance immediately before dispatch. A local fence cannot
cancel an HTTP request already in flight.

## Validation and operator fixture

[Tests](../tests/test_github_drafts.py) cover actual SQLite persistence, concurrent claims, altered
plans, foreign or human-edited objects, provider policy/content changes, malformed/oversized input,
lost replies before and after creation, failed confirmation persistence and an actual process exit
at the send boundary. A literal-loopback HTTP server also creates the simulated object and drops
its reply; read reconciliation finds it without a second POST. These fixtures are not GitHub.

The explicitly authorized real disposable repository was exercised separately: a fixed inert text
file was staged, an actual draft opened, the same operation repeated without creating a second PR,
the original object reconciled, then the draft closed and its unchanged owned branch removed.
The trusted [fixture script](../scripts/qualify_github_draft.py) performs staging and operator cleanup;
those actions are not implementations of runtime staging or compensation. It uses the operator's
existing `gh` credential; this does not qualify production credential custody or least privilege.

The current fixture requires a complete one-file `README.md` baseline, stages only a fixed
comment-only Python file and uses `dispatch_patch` / `reconcile_patch`. It refuses unexpected
baseline contents or Git tree mismatches. The exact installed development wheel was exercised
against this path, including retained receipts and cleanup. Its contract contains explicitly
unexecuted fixture identifiers, not checker acceptance or consensus authority; the report records
those limits. Never substitute this operator smoke for the governed runtime effect lifecycle.

Run this script only against an explicitly authorized disposable repository with its expected
numeric ID and disabled Actions. It creates one operation, never merges, writes only fixed inert
text, uses bounded API calls, and retains its journal if an outcome is unknown. Do not run it in a
privileged workflow against untrusted source. `--require-installed` rejects a source-checkout
import and requires `--wheel` to compare installed package files with the exact supplied archive.
The report records that wheel's SHA-256. It is not part of ordinary CI and does not ask CI for
provider credentials.

## Remaining operational integration

Consensus effect states, funded reservations, current dispatch checks and durable ordinary reporting
are implemented as development components. Required work still includes exact patch-to-Git staging,
production credential custody, coordinated cross-host journal recovery, governed remote
reconciliation and compensation. A provider
smoke is not `LIVE_EFFECT_QUALIFIED` for the complete 0.2 operational profile. G3 and final release
remain blocked until the full path and all required failure windows are qualified.
