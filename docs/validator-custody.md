# Validator custody and maintenance

Validator signing state belongs to one live process on one host. Application snapshots,
administrative approvals and a matching state hash do not prove that ownership. This guide
defines the operator checks needed for maintenance; multi-host qualification is still pending.

## Local service boundary

The packaged `data/validator.service` is an operator-reviewed systemd template. Provision a
dedicated `checkedflow-validator` account, pinned CometBFT executable and protected validator
home at its declared paths. The separately provisioned `checkedflow-abci.service` must run the
appropriate application with protected configuration and, for inherited genesis, the
[succession startup inputs](succession-approval.md#startup-admission). No service is installed
or enabled by the Python package.

The validator command takes an exclusive nonblocking lock on a stable `custody.lock` inode
before executing CometBFT. `flock --no-fork` leaves the lock held by the executed process.
A conflict exits with 73, and systemd does not restart that conflict. Never unlink or replace
the lock file while a holder exists. Use a local filesystem, not a shared network mount.
These semantics follow the [util-linux flock manual](https://man7.org/linux/man-pages/man1/flock.1.html).

The unit stops the entire control group with SIGTERM. It does not force a stuck process to
exit after the stop deadline. A failed stop is a failed custody handoff: investigate and verify
termination rather than starting a replacement. Keep systemd unit/configuration editing and
the validator directory unavailable to candidates and gateway/worker accounts.

The lock only coordinates processes using the same inode on this host. It cannot prevent a
copied private key on another host, an operator bypassing the wrapper, or old signing state
being restored. Tests exercise real Linux descriptor exclusion with a child process; they do
not establish that an installed CometBFT deployment or a cross-host handoff is qualified.

## Compatible binary upgrade

1. Verify the replacement artifact digest and its compatibility with the current protocol,
   state format and CometBFT version. Do not infer compatibility from a package version alone.
2. Confirm all four current validators are synchronized and agree at a common committed
   height. Retain authenticated observations and the current configuration digests.
3. Drain and stop one node's work services, then its validator and ABCI services. Verify their
   control groups are empty and that no independently launched process owns the validator
   home or signing key. Keep the other three validators running and verify continued progress.
4. Retain the original home, private signing key and latest anti-double-sign state together.
   Upgrade only the executable/environment. Do not run `cometbft init`, clear consensus data,
   reset signing height, or restore an older key/state pair.
5. Restart ABCI, then the validator using the same protected home and lock. Verify catch-up,
   common-height application hash, signing-key identity and normal participation before
   moving to the next organization. If verification fails, stop the upgrade sequence.

Once the new writer has committed incompatible data, binary downgrade is unsafe. Stop and use
forward recovery with retained evidence. Incompatible protocols use an approved maintenance
window and successor chain; mixed-version rolling deployment is not supported by that path.

## Node replacement and incompatible succession

Record source and destination host identities, validator public key, chain, committed
checkpoint, private custody handoff reference and signing-state digest in the protected
maintenance record. Do not publish key bytes or private host access details.

For replacement, stop and disable the source node and every service able to sign or dispatch.
Verify process absence locally on the source; an unreachable host is not evidence of shutdown.
Fence source restart through the organization's deployment/access controls before transferring
the latest home and signing state over its approved private channel. Verify receipt and digests
without starting the destination. Never activate both copies. Retain the retired source copy
only in inaccessible recovery custody; unlocking it requires another reviewed handoff.

For succession, first pause and drain old dispatch. Preserve unknown effects and unresolved
work instead of rerunning them. Obtain the final checkpoint only after the old chain is stopped
at the coordinated maintenance boundary, and retain full old history and artifacts. Review the
prepared successor and obtain both administrations' approvals with protected approval journals.
Use the approved new validator keys and corresponding fresh chain-specific signing state;
never reinitialize an existing active home to obtain these files. Verify all four nodes accept
the same approved genesis, then explicitly authorize new dispatch. Retain the old deployment
in a non-dispatching state. Approval signatures alone do not prove any of these physical steps.

The cutover point is the first new-chain commitment or externally dispatched action. After that
point, restarting old dispatch can duplicate work or spend a stale allowance. Recover forward
and reconcile obligations under current authority instead. Complete the four-host migration,
key rotation/replacement and failure-window tests before claiming this procedure qualifies G6.
