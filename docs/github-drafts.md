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

The Git tree comparison is not independent reconstruction of the approved patch. The owning
executor must verify that the staged Git objects actually encode the checked immutable source and
patch before supplying these bindings. Repository staging and that domain integration remain
unfinished. The direct provider API must not be advertised as an approved-patch-to-PR workflow.

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

Run this script only against an explicitly authorized disposable repository with its expected
numeric ID and disabled Actions. It creates one operation, never merges, writes only fixed inert
text, uses bounded API calls, and retains its journal if an outcome is unknown. Do not run it in a
privileged workflow against untrusted source. `--require-installed` rejects a source-checkout
import and requires `--wheel` to compare installed package files with the exact supplied archive.
The report records that wheel's SHA-256. It is not part of ordinary CI and does not ask CI for
provider credentials.

## Remaining operational integration

Required work still includes a consensus-bound effect state machine, separate current action
authorization, exact patch-to-Git staging, funded dispatch/fences, credential custody, supervised
freshness, coordinated journal recovery, governed reconciliation and compensation. A provider
smoke is not `LIVE_EFFECT_QUALIFIED` for the complete 0.2 operational profile. G3 and final release
remain blocked until the full path and all required failure windows are qualified.
