# Research to implementation map

Hashes identify reviewed bytes, not correctness. Local newer software snapshots are distinguished from catalogue revisions. No runtime dependency or copied implementation from these resources.

The [machine-readable registry](../src/checkedflow/data/research.json) records every reviewed version, document hash, source locator, implementation symbol, test and limitation. Catalogue summaries are attributed paraphrases from the source index. Papers retain their original licenses; their archives are not distributed.

The later A2A/MCP adapters are interoperability infrastructure, separate from these 37 research
entries. Their official SDKs are optional package dependencies; they do not supply any research
guarantee or alter the extracted core principles. [Agent communication](interoperability.md)
records their reviewed protocol/SDK versions and the implemented service profile.

## How to interpret the mapping

The extraction selects operational rules shared by the sources: separate claims from authority,
charge verification, preserve uncertainty, scope reuse, propagate withdrawal and bound iteration.
It does not merge all source APIs or import their runtimes. The original
[catalogue](https://kadubon.github.io/github.io/collective-intelligence-index.html) is the inventory;
the registry distinguishes its observed revision from each locally reviewed software version.

Each row identifies an implemented principle and a test location. A source-byte hash identifies
the reviewed input; it does not establish that a paper's assumptions hold for this runtime.
The review read source documentation, paper abstracts and structure, with selected definitions,
proof conditions and implementation paths. It is not an independent verification of every theorem.
Read each registry entry's `not_implemented_or_claimed` field before citing a guarantee.

For example, a finite behavior digest supports deduplication within a declared mission/domain.
It does not measure general originality. Paid verification slots implement a capacity constraint;
they do not reproduce a verifier-ecology model's convergence theorem. The
[plan audit](audit.md) keeps these implementation limits separate from test completion.

## Resource coverage

| Resource | Reviewed version | Adopted principle | Implementation | Verification |
|---|---|---|---|---|
| [Observing and Accelerating Collective Capability Growth](https://doi.org/10.5281/zenodo.22604358) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Charge formation and verification; compare a finite matched scratch condition. | `accounting` | [test_core](../tests/test_core.py) |
| [Verifier Ecology Theory: Packetized Self-Verification Under Residual Accountability](https://doi.org/10.5281/zenodo.21147093) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Keep verifier scope, unresolved obligations and paid verification slots. | `_vote` | [test_residuals](../tests/test_residuals.py) |
| [Executable Capability Percolation Theory](https://doi.org/10.5281/zenodo.20535654) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Represent executable dependencies as a finite acyclic capability graph. | `_eligible` | [test_residuals](../tests/test_residuals.py) |
| [Abstraction Liquidity Theory](https://doi.org/10.5281/zenodo.20476200) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Bind reusable abstractions to the receiving contract and charge construction. | `_eligible` | [test_core](../tests/test_core.py) |
| [Certified Autocatalytic Intelligence Theory: Net-Growth Certificate Algebra for Verified Capability Capital](https://doi.org/10.5281/zenodo.20061296) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Separate unique finite behaviors, copies, external inputs and withdrawn records. | `accounting` | [test_core](../tests/test_core.py) |
| [Layered Online Service and Replay Control for Verified AI R and D Acceleration](https://doi.org/10.5281/zenodo.19836225) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Keep service attempts and evidence replayable without promoting a model claim. | `replay` | [test_abci](../tests/test_abci.py) |
| [Collective Phase Transitions beyond Individual Saturation](https://doi.org/10.5281/zenodo.17853555) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Separate independent organization coordination from individual skill execution. | `_admin` | [test_abci](../tests/test_abci.py) |
| [Audit-Closed AI Scientist Protocol](https://doi.org/10.5281/zenodo.18728589) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Tie acceptance to a predeclared checker and retain failed observations. | `_vote` | [test_residuals](../tests/test_residuals.py) |
| [Reusable Consequence States Under Partial Support and Model Uncertainty](https://doi.org/10.5281/zenodo.22170023) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Represent unknown consequences explicitly and prohibit automatic retry. | `_reconcile` | [test_residuals](../tests/test_residuals.py) |
| [Evidence-Carrying Operational Claims in Open Systems: Physical Ledgers, Typed Interfaces, and One-Sided Deployment Guarantees](https://doi.org/10.5281/zenodo.21531413) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Separate signed claims from execution authority and committed state. | `transition` | [test_abci](../tests/test_abci.py) |
| [Bottleneck Inversion Theory: Machine-Readable Witness Calculus for Unlockable Potential](https://doi.org/10.5281/zenodo.20545356) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Expose verification as a protected capacity constraint; defer inversion analysis. | `_lease` | [test_core](../tests/test_core.py) |
| [Salience-Queue Occupation Theory](https://doi.org/10.5281/zenodo.20526451) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Reserve verification capacity before admitting more candidate work. | `_propose` | [test_core](../tests/test_core.py) |
| [Constraint Generative Theory: Typed Constraint Effects and Scientific Availability](https://doi.org/10.5281/zenodo.20199440) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Make admissibility constraints explicit in portable command contracts. | `transition` | [test_boundaries](../tests/test_boundaries.py) |
| [Certified Conversion Networks for AI Workflows](https://doi.org/10.5281/zenodo.19994795) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Charge all phase conversions to a bounded common mission ledger. | `_finish` | [test_core](../tests/test_core.py) |
| [Certified Service Is Not Enough for Long-Running AGI](https://doi.org/10.5281/zenodo.19719004) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Expire memory and carry unresolved obligations across attempts. | `advance` | [test_residuals](../tests/test_residuals.py) |
| [Controller Scale Is Not Enough for Long-Running AGI: A Workflow Theory with Reusable Certified Libraries](https://doi.org/10.5281/zenodo.19690749) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Reuse checked procedures as dependencies in subsequent bounded synthesis. | `_propose` | [test_boundaries](../tests/test_boundaries.py) |
| [When Should Inference Be Split? A Fixed-Budget Theory of Predictable Multi-Agent Advantage under Local Context Ceilings](https://doi.org/10.5281/zenodo.18932509) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Compare work under the same declared resource ceiling; defer advantage bounds. | `accounting` | [test_integration](../tests/test_integration.py) |
| [Stop Recomputing for AI/LLMs: Proof-Carrying Skills for Compute-Saving Inference Reuse](https://doi.org/10.5281/zenodo.18490939) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Require evidence and receiver compatibility before reusing a skill. | `_eligible` | [test_core](../tests/test_core.py) |
| [Verification-Limited Intelligence Acceleration: Observable-Only Laws, Bounded Derivation, and Diagnostics under No-Meta Constraints](https://doi.org/10.5281/zenodo.18436828) | `7bd9fe246ae0f5a4bf6564b588c0426b5b7f29bd` | Bound candidates and verification work; leave acceleration unidentified. | `accounting` | [test_core](../tests/test_core.py) |
| [collective-capability-runtime](https://github.com/kadubon/collective-capability-runtime) | `1.9.0` | Use committed leases, fencing, disagreement records and separate growth coordinates. | `_lease` | [test_abci](../tests/test_abci.py) |
| [percolation-inversion-compiler](https://github.com/kadubon/percolation-inversion-compiler) | `1.1.0` | Carry evidence across explicit dependency boundaries; defer compiler witnesses. | `_eligible` | [test_core](../tests/test_core.py) |
| [verification-ecology-kit](https://github.com/kadubon/verification-ecology-kit) | `1.3.0` | Fund verifier work and preserve residual obligations. | `_propose` | [test_residuals](../tests/test_residuals.py) |
| [alt-foundry-kernel](https://github.com/kadubon/alt-foundry-kernel) | `0.5.0` | Track receiver-relative reusable procedure formation and dependencies. | `_propose` | [test_boundaries](../tests/test_boundaries.py) |
| [cait-certificate-schema](https://github.com/kadubon/cait-certificate-schema) | `0.2.0` | Keep novelty, copies, external input and losses separately typed. | `accounting` | [test_core](../tests/test_core.py) |
| [collective-phase-control-fabric](https://github.com/kadubon/collective-phase-control-fabric) | `1.0.1` | Apply finite governance changes independently of workcell execution. | `_admin` | [test_core](../tests/test_core.py) |
| [observable-agent-workflow-memory](https://github.com/kadubon/observable-agent-workflow-memory) | `0.2.0b0` | Keep workflow memory observable and replayable. | `replay` | [test_abci](../tests/test_abci.py) |
| [oasg](https://github.com/kadubon/oasg) | `1.2.0` | Admit generated procedures only through a fixed validation boundary. | `_vote` | [test_integration](../tests/test_integration.py) |
| [audit-closed-ai-scientist](https://github.com/kadubon/audit-closed-ai-scientist) | `0.2.0` | Fix the validation contract before generation, recording rejection. | `_register_verifier` | [test_residuals](../tests/test_residuals.py) |
| [loscr](https://github.com/kadubon/loscr) | `0.1.0` | Retain uncertainty and replay records without asserting acceleration. | `_residual` | [test_residuals](../tests/test_residuals.py) |
| [asi-proxy-phase-growth-simulator](https://github.com/kadubon/asi-proxy-phase-growth-simulator) | `0.1.0` | Label the bounded demonstration as a laboratory comparison, not a forecast. | `accounting` | [test_integration](../tests/test_integration.py) |
| [asi-proxy-phase-skill](https://github.com/kadubon/asi-proxy-phase-skill) | `1.1.0` | Expose an offline machine-readable command and evidence vocabulary. | `transition` | [test_boundaries](../tests/test_boundaries.py) |
| [certified-memory-governance-layer](https://github.com/kadubon/certified-memory-governance-layer) | `1.1.2` | Withdraw invalid memory and quarantine downstream procedures. | `_invalidate` | [test_residuals](../tests/test_residuals.py) |
| [memoryflow-agent-memory-auditor](https://github.com/kadubon/memoryflow-agent-memory-auditor) | `0.1.0` | Preserve memory provenance, expiry and reuse visibility. | `advance` | [test_core](../tests/test_core.py) |
| [problem-frame-gate](https://github.com/kadubon/problem-frame-gate) | `1.1.0` | Require an explicit problem specification and predeclared receiver scope. | `_create_task` | [test_core](../tests/test_core.py) |
| [fost-agent-ledger](https://github.com/kadubon/fost-agent-ledger) | `2.0.0` | Keep physical execution receipts distinct from claimed results. | `_finish` | [test_abci](../tests/test_abci.py) |
| [agent-trust-residual-benchmark](https://github.com/kadubon/agent-trust-residual-benchmark) | `0.2.0` | Use failures and unresolved obligations as retained test outcomes. | `_residual` | [test_residuals](../tests/test_residuals.py) |
| [Oversight-Centered-Metrology-PoC](https://github.com/kadubon/Oversight-Centered-Metrology-PoC) | `undeclared` | Require independent oversight signatures; defer model-based truth estimation. | `_vote` | [test_residuals](../tests/test_residuals.py) |

## Interpretation limits

The [A2A/MCP extension](conformance.md) supplies interoperability, not new evidence for a research
claim. Notifications, protocol task completion and discoverable tools are delivery mechanisms.
The adopted principles still live in signed authority checks, budget reservations, residuals,
independent verification and conditional reuse. The [security audit](security.md) examines those
boundaries separately from the research correspondence.

Finite-domain equality establishes equality on the 31 declared inputs only. Reuse can reduce grammar search while increasing construction, verification or operation cost. The demonstration reports both conditions, including initial formation, and does not infer global acceleration, AGI/ASI capability, universal reproduction ratios, information-theoretic bounds or real organizational independence from a single-host test.
