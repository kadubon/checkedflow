# A first run, from source to evidence

This tutorial progresses from local synthesis to independently checked work. To connect an
agent after understanding the lifecycle, follow [A2A and MCP setup](interoperability.md).
Protocol discovery can describe available actions; it cannot create the required signing authority.

## 1. Inspect an actual generated candidate

From the source directory, on Windows or Linux:

```sh
uv sync --frozen --all-extras --group dev
uv run checkedflow --version
uv run checkedflow example
```

The version is `0.1.0`. The example's JSON includes:

```json
{
  "verification": "not_executed",
  "candidate": {
    "operations": ["double", "increment"],
    "dependencies": [],
    "candidates_tried": 8,
    "source": "def solve(values):\n    result = list(values)\n    result = [x * 2 for x in result]\n    result = [x + 1 for x in result]\n    return result\n"
  }
}
```

The source implements the transformation `[-1, 2] → [-1, 5]`. Eight grammar candidates were
examined in this configuration. No Python candidate has executed, no checker has voted, and
there is no capability record yet. Use this step to inspect the generator contract.

## 2. Apply a signed state transition

```sh
uv run python -m checkedflow.data.examples.sdk
```

Read the [complete SDK example](../src/checkedflow/data/examples/sdk.py). It performs four steps:

1. Create four ephemeral organization keys and a separate worker key.
2. Construct `genesis("sdk-example", organization_public_keys)`.
3. Sign one `worker.register` command with three organization keys.
4. Apply that envelope at height 1 to two `Runtime` instances and compare their hashes.

The JSON output includes `registered_worker: "worker0"`, `height: 1` and
`replay_matches: true`. This demonstrates authorization and deterministic replay. The example
does not save private keys, start a node, or claim that code has passed verification.

Every command has a chain, actor, sequential nonce, request ID, kind and payload. A request ID
can be safely resubmitted only with identical command bytes. To inspect exact fields:

```sh
uv run checkedflow schema commands
uv run checkedflow schema envelope
```

`checkedflow validate` checks envelope structure. Signature verification requires the committed
key registry and occurs in `Runtime.apply` or the ABCI application. These are distinct checks.

## 3. Follow the complete formation loop

Provision Linux infrastructure with [operations.md](operations.md), then use the `demo` command
in the [README](../README.md). Windows can run the SDK and inspect results; the sandbox runs on
Linux. The laboratory uses three targets: `double`, `double → increment`, and
`double → increment → reverse`. For each target it:

1. Commits a task with a fixed cost and dependency list.
2. Commits a lease and a start before the worker acts.
3. Generates source and commits a receipt containing the exact source and digest.
4. Proposes that source as a capability candidate and reserves four verification slots.
5. Runs the candidate over all 31 inputs through each organization's gVisor checker.
6. Admits matching evidence, then uses the checked source in the next generation.

The process repeats from scratch under a separate mission with the same limits. The final
`acceptance.json` compares results, includes both mission ledgers, and records a common state hash.
Read failed or unknown outcomes before interpreting the comparison as an improvement.

## 4. Interpret a budget without hiding verification

This table is an illustrative single-candidate mission with budget 20, generation cost 6,
verification cost 1 per organization, and protected verification reserve 4:

| After… | Spent | Reserved | Available | Meaning |
|---|---:|---:|---:|---|
| Mission approval | 0 | 0 | 20 | Protection is an admission floor, not yet an allocation. |
| Generation lease | 0 | 6 | 14 | Six units are reserved for this attempt. |
| Generation finish | 6 | 0 | 14 | Completion charges the full declared ceiling. |
| Candidate proposal | 6 | 4 | 10 | Four checks are funded before acceptance. |
| Three passing checks | 9 | 1 | 10 | The artifact is checked; the fourth check remains funded. |
| Fourth passing check | 10 | 0 | 10 | No outstanding reservation remains. |

If generation times out, its six units are still charged and its outcome is unknown. If a check
fails, quarantine releases unstarted verification reservations; already running checks remain
charged on finish or timeout. `available` alone is not the producer's spendable amount because
the protected verification floor also applies. See [state transitions](state-machine.md).

## 5. Recover before repeating work

Restarting the bundled worker after generation finish can resume the **proposal** from its
persisted receipt; it does not repeat generation. A started task without a committed result is
not automatically rerun. It eventually expires into uncertainty and requires reconciliation.
See [worker recovery boundaries](operations.md#worker-recovery-boundaries) for each interruption.

For event reconstruction, inspect `checkedflow schema block` and use
`checkedflow replay --genesis genesis.json --blocks-jsonl blocks.jsonl`. The input is one block
record per line, in order, including empty blocks and rejected transactions. A matching replay
hash verifies consistency with that log; the log's consensus provenance must be checked separately.

## 6. Connect an agent after understanding the lifecycle

Install `checkedflow[agents,distributed]`, keep the own-node RPC local, and bind a gateway to an
existing mission. Start with MCP stdio to discover `checkedflow://profile`, then inspect
`checkedflow://mission` for budget and signer nonces. Use the `checkedflow_review` prompt for a
task or complete an ID from the resource templates. These steps need no mutation authority.

For A2A, discover the Agent Card and send `operation=profile`; use `operation=submit` for an
acknowledgment or `operation=task` to observe the work lifecycle. Obtain a legitimately signed
envelope before submission. Follow [the full communication guide](interoperability.md) for tokens,
exact JSON fields, history, streaming and callbacks. After a lost response, inspect the original
task and command identity before recovery; creating a new ID is not an idempotent retry.
