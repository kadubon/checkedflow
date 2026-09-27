# Staging an approved patch for a draft

Status: development SDK component. Source fixtures cover finite staging and interrupted recovery.
This is not the complete G3 workflow or permission to publish 0.2.0.

## Why staging is a separate permission

A draft needs a branch containing its proposed changes. Creating that branch is already an
external effect: repository integrations can see it before a pull request exists. CheckedFlow
therefore requires explicit staging permission, the exact accepted patch, disabled destination
Actions and current consensus authority before each write. Candidate code never receives GitHub
credentials. This profile does not update existing references, merge or delete branches.

The default `Dispatcher(..., staging=False)` expects an already staged, approved head. To create
that head, the operator must use `staging=True` and a protected policy with profile
`checkedflow/effect-policy/v2` and boolean `staging: true`. Inspect its closed schema with
`checkedflow schema effect-staging-policy`. All v1 policy identity, size, intent and ordering rules
still apply. The quorum-approved effect must bind the new policy digest. A v1 policy cannot
silently acquire permission to create Git objects. The supervisor checks this before reservation.

## Plan before authorization

Reconstruct the accepted result using complete source and patch bytes. The pure SDK function
`git_staging.staging_commit(contract, result)` returns the planned Git head ID and create-commit
payload. Put that head in the intent before policy review and quorum authorization. A random or
previously chosen head is rejected by staging. Git SHA-1 identifies provider objects; SHA-256
continues to identify application contracts, patches and evidence.

The commit has exactly one parent, the contract's base commit, and the complete result tree.
Its message binds the contract and patch digests. Author and committer are the synthetic identity
`CheckedFlow <checkedflow@example.invalid>`, with fixed date `2000-01-01T00:00:00Z`.
That date is a serialization constant, not the time the effect occurred or a human attribution.
The real observation remains separate evidence. No generated code, checkout filters or hooks
run to compute these identities.

## Bounded write sequence

The provider retains a durable unknown claim before the first possible write. One first
invocation can perform at most these four POST requests:

1. Create the complete tree from accepted UTF-8 regular files, without inheriting a remote tree.
2. Create the deterministic commit and require the returned ID to equal the approved head.
3. Create a new reference in the operation-owned namespace. An existing reference is a conflict.
4. Recheck the exact remote head/tree/parent, then create the owned draft with maintainer edits
   disabled. An already visible exact owned draft can instead be observed without creating it.

Before every POST, the adapter rechecks repository identity, disabled Actions and unchanged
base, invokes the dispatcher's current authority/evidence check, and rechecks local enablement.
It checks each returned object before proceeding. The sequence uses the GitHub
[tree](https://docs.github.com/en/rest/git/trees),
[commit](https://docs.github.com/en/rest/git/commits) and
[create-reference](https://docs.github.com/en/rest/git/refs#create-a-reference) APIs.
It never uses the reference-update API. Remote changes after a check and requests already in
flight cannot be cancelled by a local fence; this is not an atomic transaction across systems.

## Failure and recovery

A rejected, lost or malformed reply stops subsequent writes. The claim remains unknown.
A later invocation returns its retained outcome; it never resumes a partially completed
sequence or creates another operation. A process death after the supervisor invocation claim
also prevents another dispatch. The supervisor journal binds staging mode, so recovery cannot
switch an operation between modes.

An interrupted sequence may leave immutable objects or its owned branch without a draft.
Those objects are not evidence of a completed effect. Preserve journals, original policy,
artifact references and obligations. Use [historical reconciliation](effect-reconciliation.md)
for a separate observation and administrative review. Absence does not authorize resending.
Automated compensation, retirement, least-privilege credential lifecycle and full multi-host
qualification remain separate unfinished requirements.

Source tests inject lost replies before and after each of the four writes, malformed replies,
revocation at every write boundary, existing references, process interruption, policy errors,
changed recovery mode and historical receipts. They use a provider fixture. A real disposable
repository smoke must use the installed artifact and report its narrower scope explicitly.
