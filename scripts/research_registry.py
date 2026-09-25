"""Rebuild provenance from already available local sources. Makes no network requests."""

import argparse

# Long bibliography strings are intentionally kept as complete source records.
# ruff: noqa: E501
import hashlib
import json
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]

# Deliberately narrow operational extractions; these are not claims of implementing each theory.
PRINCIPLES = {
    "paper-growth": (
        "Charge formation and verification; compare a finite matched scratch condition.",
        "accounting",
        "tests/test_core.py",
    ),
    "paper-vet": (
        "Keep verifier scope, unresolved obligations and paid verification slots.",
        "_vote",
        "tests/test_residuals.py",
    ),
    "paper-ecpt": (
        "Represent executable dependencies as a finite acyclic capability graph.",
        "_eligible",
        "tests/test_residuals.py",
    ),
    "paper-alt": (
        "Bind reusable abstractions to the receiving contract and charge construction.",
        "_eligible",
        "tests/test_core.py",
    ),
    "paper-cait": (
        "Separate unique finite behaviors, copies, external inputs and withdrawn records.",
        "accounting",
        "tests/test_core.py",
    ),
    "paper-loscr": (
        "Keep service attempts and evidence replayable without promoting a model claim.",
        "replay",
        "tests/test_abci.py",
    ),
    "paper-collective-phase": (
        "Separate independent organization coordination from individual skill execution.",
        "_admin",
        "tests/test_abci.py",
    ),
    "paper-audit-closed": (
        "Tie acceptance to a predeclared checker and retain failed observations.",
        "_vote",
        "tests/test_residuals.py",
    ),
    "paper-consequence": (
        "Represent unknown consequences explicitly and prohibit automatic retry.",
        "_reconcile",
        "tests/test_residuals.py",
    ),
    "paper-operational-claims": (
        "Separate signed claims from execution authority and committed state.",
        "transition",
        "tests/test_abci.py",
    ),
    "paper-bit": (
        "Expose verification as a protected capacity constraint; defer inversion analysis.",
        "_lease",
        "tests/test_core.py",
    ),
    "paper-sqot": (
        "Reserve verification capacity before admitting more candidate work.",
        "_propose",
        "tests/test_core.py",
    ),
    "paper-cgt": (
        "Make admissibility constraints explicit in portable command contracts.",
        "transition",
        "tests/test_boundaries.py",
    ),
    "paper-conversion": (
        "Charge all phase conversions to a bounded common mission ledger.",
        "_finish",
        "tests/test_core.py",
    ),
    "paper-continuity": (
        "Expire memory and carry unresolved obligations across attempts.",
        "advance",
        "tests/test_residuals.py",
    ),
    "paper-workflow-library": (
        "Reuse checked procedures as dependencies in subsequent bounded synthesis.",
        "_propose",
        "tests/test_boundaries.py",
    ),
    "paper-split-inference": (
        "Compare work under the same declared resource ceiling; defer advantage bounds.",
        "accounting",
        "tests/test_integration.py",
    ),
    "paper-pcs": (
        "Require evidence and receiver compatibility before reusing a skill.",
        "_eligible",
        "tests/test_core.py",
    ),
    "paper-verification-limited": (
        "Bound candidates and verification work; leave acceleration unidentified.",
        "accounting",
        "tests/test_core.py",
    ),
    "sw-ccr": (
        "Use committed leases, fencing, disagreement records and separate growth coordinates.",
        "_lease",
        "tests/test_abci.py",
    ),
    "sw-pic": (
        "Carry evidence across explicit dependency boundaries; defer compiler witnesses.",
        "_eligible",
        "tests/test_core.py",
    ),
    "sw-vek": (
        "Fund verifier work and preserve residual obligations.",
        "_propose",
        "tests/test_residuals.py",
    ),
    "sw-alt": (
        "Track receiver-relative reusable procedure formation and dependencies.",
        "_propose",
        "tests/test_boundaries.py",
    ),
    "sw-cait": (
        "Keep novelty, copies, external input and losses separately typed.",
        "accounting",
        "tests/test_core.py",
    ),
    "sw-cpcf": (
        "Apply finite governance changes independently of workcell execution.",
        "_admin",
        "tests/test_core.py",
    ),
    "sw-oawm": ("Keep workflow memory observable and replayable.", "replay", "tests/test_abci.py"),
    "sw-oasg": (
        "Admit generated procedures only through a fixed validation boundary.",
        "_vote",
        "tests/test_integration.py",
    ),
    "sw-audit": (
        "Fix the validation contract before generation, recording rejection.",
        "_register_verifier",
        "tests/test_residuals.py",
    ),
    "sw-loscr": (
        "Retain uncertainty and replay records without asserting acceleration.",
        "_residual",
        "tests/test_residuals.py",
    ),
    "sw-simulator": (
        "Label the bounded demonstration as a laboratory comparison, not a forecast.",
        "accounting",
        "tests/test_integration.py",
    ),
    "sw-skill": (
        "Expose an offline machine-readable command and evidence vocabulary.",
        "transition",
        "tests/test_boundaries.py",
    ),
    "sw-cmgl": (
        "Withdraw invalid memory and quarantine downstream procedures.",
        "_invalidate",
        "tests/test_residuals.py",
    ),
    "sw-memoryflow": (
        "Preserve memory provenance, expiry and reuse visibility.",
        "advance",
        "tests/test_core.py",
    ),
    "sw-pfg": (
        "Require an explicit problem specification and predeclared receiver scope.",
        "_create_task",
        "tests/test_core.py",
    ),
    "sw-fost": (
        "Keep physical execution receipts distinct from claimed results.",
        "_finish",
        "tests/test_abci.py",
    ),
    "sw-atrb": (
        "Use failures and unresolved obligations as retained test outcomes.",
        "_residual",
        "tests/test_residuals.py",
    ),
    "sw-oversight": (
        "Require independent oversight signatures; defer model-based truth estimation.",
        "_vote",
        "tests/test_residuals.py",
    ),
}


def build(source_root: Path) -> None:
    catalogue = source_root / "github.io/collective-intelligence-index.json"
    evidence_path = source_root / "github.io/data/collective-intelligence-evidence.json"
    index = json.loads(catalogue.read_text(encoding="utf-8"))
    evidence = {
        row["id"]: row for row in json.loads(evidence_path.read_text(encoding="utf-8"))["evidence"]
    }
    records = []
    for resource in index["resources"]:
        identity = resource["id"]
        principle, symbol, test = PRINCIPLES[identity]
        refs = [evidence[key] for key in resource["evidence_refs"]]
        observed = []
        if resource["kind"] == "paper":
            reference = refs[0]
            path = (
                source_root
                / "paper-tex-backup"
                / unquote(urlparse(reference["url"]).path.split("/")[-1])
            )
            fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
            assert fingerprint == reference["sha256"], path
            version = reference["revision"]
            observed.append({"file": path.name, "sha256": fingerprint, "matches_catalogue": True})
            scope = "Abstract and section structure; operational definitions and limitations where relevant. No independent theorem proof audit."
        else:
            candidates = sorted(
                p
                for p in source_root.glob(resource["name"] + "*")
                if p.is_dir() and (p / "README.md").is_file()
            )
            if identity == "sw-oversight":
                candidates = [source_root / "oversight-centered-poc-gemma3-1b"]
            assert candidates, identity
            directory = source_root / resource["name"]
            if not (directory / "README.md").is_file():
                directory = candidates[-1]
            project = directory / "pyproject.toml"
            version = (
                tomllib.loads(project.read_text(encoding="utf-8"))
                .get("project", {})
                .get("version", "undeclared")
                if project.exists()
                else "undeclared"
            )
            selected = {"README.md", "pyproject.toml"}
            for reference in refs:
                if "/blob/" in reference["url"]:
                    selected.add(unquote(reference["url"].split("/blob/", 1)[1].split("/", 1)[1]))
            for relative in sorted(selected):
                path = directory / relative
                if path.exists() and path.is_file():
                    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
                    expected = next(
                        (e["sha256"] for e in refs if e["url"].endswith("/" + relative)), None
                    )
                    observed.append(
                        {
                            "file": relative,
                            "sha256": fingerprint,
                            "matches_catalogue": fingerprint == expected if expected else None,
                        }
                    )
            scope = "Local README, package metadata and available referenced design documents; selected lease and verification reducers. Local checkout revision not attested."
        records.append(
            {
                "id": identity,
                "kind": resource["kind"],
                "title": resource["name"],
                "source": resource["canonical_url"],
                "catalogue_summary": resource["summary"]["en"],
                "reviewed_version": version,
                "reviewed_on": "2026-09-25",
                "review_scope": scope,
                "observed_content": observed,
                "catalogue_evidence": [
                    {k: e[k] for k in ("url", "revision", "sha256", "locator")} for e in refs
                ],
                "adopted_principle": principle,
                "implementation": {"file": "src/checkedflow/core/machine.py", "symbol": symbol},
                "verification": [test],
                "not_implemented_or_claimed": resource["limitations"]["en"],
                "status": "principle_extraction_only",
            }
        )
    output = {
        "schema_version": "1",
        "source_index": index["canonical_url"],
        "index_sha256": hashlib.sha256(catalogue.read_bytes()).hexdigest(),
        "evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "counts": {"software": 18, "papers": 19},
        "boundary": "Hashes identify reviewed bytes, not correctness. Local newer software snapshots are distinguished from catalogue revisions. No runtime dependency or copied implementation from these resources.",
        "resources": records,
    }
    (ROOT / "src/checkedflow/data/research.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    lines = [
        "# Research to implementation map",
        "",
        output["boundary"],
        "",
        "The [machine-readable registry](../src/checkedflow/data/research.json) records every reviewed version, document hash, source locator, implementation symbol, test and limitation. Catalogue summaries are attributed paraphrases from the source index. Papers retain their original licenses; their archives are not distributed.",
        "",
        "| Resource | Reviewed version | Adopted principle | Implementation | Verification |",
        "|---|---|---|---|---|",
    ]
    for row in records:
        lines.append(
            f"| [{row['title']}]({row['source']}) | `{row['reviewed_version']}` | {row['adopted_principle']} | `{row['implementation']['symbol']}` | [{Path(row['verification'][0]).stem}](../{row['verification'][0]}) |"
        )
    lines += [
        "",
        "## Interpretation limits",
        "",
        "Finite-domain equality establishes equality on the 31 declared inputs only. Reuse can reduce grammar search while increasing construction, verification or operation cost. The demonstration reports both conditions, including initial formation, and does not infer global acceleration, AGI/ASI capability, universal reproduction ratios, information-theoretic bounds or real organizational independence from a single-host test.",
    ]
    (docs / "research.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    build(parser.parse_args().source_root)
