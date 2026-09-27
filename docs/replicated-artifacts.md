# Checking artifact availability across replicas

Status: development storage adapter. Separate-host placement and the full operational profile
still require qualification. Four names or four namespaces do not establish four failure domains.

## The problem

A patch may have passed verification while its source bytes have since disappeared. A committed
digest cannot recreate those bytes. `ReplicatedStore` implements an explicit service admission
rule: each read must freshly retrieve and verify the artifact from at least three of four
operator-configured backends. It returns verified bytes for the immediate operation. An outage
does not rewrite the candidate's consensus acceptance.

The four backends implement `ArtifactStore`. They can be local stores, authenticated S3 stores
or appropriately retained stores. The operator supplies unique sorted names and distinct backend
instances. This detects accidentally counting the same instance twice; it cannot detect two
configured clients pointing at the same disk or service. Endpoint authentication, independent
placement, credentials and lifecycle policy remain protected deployment configuration.

## SDK and limits

```python
from checkedflow.replicated_artifacts import Replica, ReplicatedStore

# stores is a trusted tuple of four configured ArtifactStore backends.
replicated = ReplicatedStore(tuple(
    Replica(f"operator-{index}", store) for index, store in enumerate(stores)
))
body = replicated.get(reference, access=trusted_access)
observation = replicated.inspect(reference, access=trusted_access)
```

Supply this adapter as the store to existing verification, reuse and effect SDKs. No consensus
transition imports it or performs network I/O. For an executor, missing replicas prevent dispatch;
an availability failure after an external effect keeps that effect unresolved, not re-executable.

`get` probes all four backends once and independently verifies binary type, exact length and
SHA-256 on every returned value. Three or four valid replies permit the read. Fewer raise stable
`UNAVAILABLE`. A missing object, corruption, access failure or provider exception never counts as
a verified copy. A transient outage does not trigger background repair or mutate global acceptance.
Process interruption is not swallowed. Backend diagnostics should be retained in protected backend
logs; arbitrary exception strings never appear in the portable observation.

`put` requires both write and read permission before consuming its source or contacting backends.
It verifies the bounded complete input before making at most four publication calls, then performs
fresh readback from all four. A lost write acknowledgement can still succeed if enough complete
copies are readable. A success acknowledgement without readable bytes cannot succeed. There is no
automatic retry within a call. Failed publication leaves any complete copies intact; it does not
erase them or claim rollback. Idempotent publication of immutable bytes is distinct from repeating
candidate execution or an external PR effect.

Each backend must enforce finite I/O deadlines. The aggregate is sequential and bounded by four
backend reads, or four backend writes plus four reads, including each backend's internal calls.
It cannot interrupt an indefinitely blocked custom backend. The reference still caps an object at
4 MiB; the input and verified read buffers are bounded by that profile. These are component bounds,
not a measured distributed latency SLO.

## Portable observation

`inspect` returns an `Availability` with the exact reference and sorted `verified`/`unavailable`
name lists. They must be disjoint and partition exactly four configured names. `usable` means
only that this observation obtained at least three valid copies. It does not mean accepted,
authorized, novel, current forever, or independently replicated across organizations.

`record()` follows `checkedflow schema artifact-availability`. The schema checks the closed fields,
reference, name format and list counts. Consumers must also check disjointness, sorting and the
exact protected configured names. The record has no signature or trust root and must not be
accepted from an untrusted client as evidence of availability. Never cache it in place of a later
fresh read. Sequential reads also cannot prove simultaneous availability or continued storage after
the observation. The immediate caller has the returned verified bytes, while future use must check
again.

## Retention and failures

This adapter exposes no erase, repair, credential discovery or replica reconfiguration method.
Keep retention controllers and operation pins for each actual backend, following the
[retention contract](retention.md). Protected roots and unresolved-effect evidence must survive
maintenance. Replication does not grant permission to resurrect a tombstoned object: use the
appropriate retained view, and restore its trusted catalog floor with the data. Do not substitute
a raw backup backend to bypass retention checks.

Source tests use four real SQLite stores and exercise corruption, removal, lost acknowledgements,
false acknowledgements, invalid responses, scope denial and executor refusal below the threshold.
The installed S3 qualification extends the real service case through four namespaces, corruption,
removal and service outage. A single service exercises the API contract; it is explicitly not
multi-host durability. The four-node/gVisor executor case uses the same adapter for artifact access.
Both extended infrastructure paths need evidence tied to their own installed distribution.
