"""Selected invariant-breaking mutants must be killed by existing regression tests."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    (
        "quorum",
        "len(ctx.signers & s.organizations.keys()) >= 3",
        "len(ctx.signers & s.organizations.keys()) >= 1",
        "test_administration_cannot_be_self_authorized",
    ),
    (
        "expiry",
        "height >= task.deadline",
        "height > task.deadline",
        "test_expiry_keeps_cost_and_requires_reconciliation",
    ),
    (
        "capacity",
        'mission.verification_reserve if task.phase != "verify" else 0',
        "0",
        "test_capacity_reserves_verification_first",
    ),
    (
        "fence",
        'task.fence == integer(p["fence"], low=1)',
        'integer(p["fence"], low=1) > 0',
        "test_expiry_keeps_cost_and_requires_reconciliation",
    ),
    (
        "dedup",
        "len(identities)",
        "len(live)",
        "test_full_lifecycle_preserves_roles_and_funded_verification",
    ),
]

OPERATIONAL_MUTATIONS = [
    (
        "core/work_budget.py",
        "test_work_budget.py",
        "budget_spending_retention",
        "return sum(ticket.charged for ticket in self.tickets)",
        "return 0",
        "test_rejects_budget_excess_reconfiguration_and_terminal_rewriting",
    ),
    (
        "core/work_budget.py",
        "test_work_budget.py",
        "verification_budget_protection",
        "return max(0, self.verification_reserve - allocated)",
        "return 0",
        "test_verification_allocation_survives_other_phases_and_release",
    ),
    (
        "repository_execution.py",
        "test_repository_execution.py",
        "repository_inventory_binding",
        "digest_bytes(cases) == contract.test_inventory_digest",
        "True",
        "test_reject_before_sandbox",
    ),
    (
        "repository_execution.py",
        "test_repository_execution.py",
        "repository_timeout_uncertainty",
        'result.status == "reported"',
        "True",
        "test_unknown_and_mismatch_are_separate",
    ),
    (
        "vault_signer.py",
        "test_vault_signer.py",
        "managed_signer_credential_binding",
        "public_key.hex() == self.credential.public_key",
        "True",
        "test_metadata_and_signature_fail_closed",
    ),
    (
        "core/key_registry.py",
        "test_key_lifecycle.py",
        "retired_key_nonresurrection",
        "min(item.retired_height, activation)",
        "activation",
        "test_rotation_cannot_resurrect_an_expired_key_or_overlap_live_intervals",
    ),
    (
        "core/key_registry.py",
        "test_key_lifecycle.py",
        "key_possession_authority",
        "context.possession_key == public_key",
        "True",
        "test_registry_validation_and_proof_context_cannot_be_omitted",
    ),
    (
        "core/authority.py",
        "test_key_lifecycle.py",
        "key_activation_height",
        "self.activated_height <= height",
        "True",
        "test_pending_activation_retirement_and_nonce_continuity",
    ),
    (
        "artifacts.py",
        "test_artifacts.py",
        "artifact_digest_integrity",
        "sha256(body).hexdigest() == ref.digest",
        "True",
        "test_corruption_is_detected_not_silently_repaired",
    ),
    (
        "artifacts.py",
        "test_artifacts.py",
        "artifact_scope_authorization",
        "scope in self.scopes and permission in self.permissions",
        "True",
        "test_denial_before_storage_or_stream_observation",
    ),
    (
        "operational_storage.py",
        "test_operational_storage.py",
        "trusted_replay_checkpoint",
        "head_hash == expected_hash",
        "True",
        "test_committed_archive_reopens_and_history_replays",
    ),
    (
        "core/operational.py",
        "test_operational_runtime.py",
        "duplicate_control_application",
        "if duplicate:",
        "if False:",
        "test_replaying_an_old_pause_does_not_pause_newly_resumed_mission",
    ),
    (
        "core/request_journal.py",
        "test_request_journal.py",
        "checkpoint_nonce_continuity",
        "actors=_advance_actor(journal, command)",
        "actors=journal.actors",
        "test_ordinary_saturation_preserves_control_and_checkpoint_capacity",
    ),
    (
        "core/authority.py",
        "test_operational_identity.py",
        "organization_quorum",
        "len(organizations) >= 3",
        "len(organizations) >= 1",
        "test_three_organizations_and_purposes_are_separate",
    ),
    (
        "core/authority.py",
        "test_operational_identity.py",
        "mission_scope",
        "self.actor.mission == mission",
        "True",
        "test_worker_cannot_promote_its_role_or_mission",
    ),
    (
        "core/authority.py",
        "test_operational_identity.py",
        "key_purpose",
        "self.actor.role == role",
        "True",
        "test_worker_cannot_promote_its_role_or_mission",
    ),
    (
        "domains/repository_patch.py",
        "test_repository_patch.py",
        "patch_result_binding",
        "tree.digest == contract.result_tree",
        "True",
        "test_patch_binds_preimage_result_and_scope_without_executing",
    ),
    (
        "domains/repository_patch.py",
        "test_repository_patch.py",
        "independent_output_verdict",
        "dumps(actual) == dumps(list(expected))",
        "True",
        "test_incorrect_output_and_forged_reports_cannot_assert_success",
    ),
]


def main() -> None:
    outcomes = []
    mutations = [
        ("core/machine.py", "test_core.py", *mutation) for mutation in MUTATIONS
    ] + OPERATIONAL_MUTATIONS
    for module, suite, name, old, new, test in mutations:
        with tempfile.TemporaryDirectory(prefix="checkedflow-mutant-") as temporary:
            directory = Path(temporary)
            shutil.copytree(
                ROOT / "src/checkedflow",
                directory / "checkedflow",
                ignore=shutil.ignore_patterns("__pycache__", "proto"),
            )
            path = directory / "checkedflow" / module
            source = path.read_text()
            if old not in source:
                raise SystemExit(f"mutant no longer applies: {name}")
            path.write_text(source.replace(old, new, 1))
            environment = os.environ | {
                "PYTHONPATH": str(directory),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    str(ROOT / "tests" / suite),
                    "-k",
                    test,
                    "-q",
                    "-p",
                    "no:cacheprovider",
                ],
                env=environment,
                cwd=directory,
                capture_output=True,
                timeout=120,
            )
            killed = result.returncode == 1 and b"AssertionError" in result.stdout
            # pytest.raises failures also kill admission/expiry mutants.
            killed = killed or (result.returncode == 1 and b"DID NOT RAISE" in result.stdout)
            outcomes.append({"mutant": name, "killed": killed, "test": test})
            if not killed:
                print(
                    result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace")
                )
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/fault-injection.json").write_text(json.dumps(outcomes, indent=2))
    print(json.dumps(outcomes))
    if not all(row["killed"] for row in outcomes):
        raise SystemExit("An invariant-breaking mutation survived or failed to run.")


if __name__ == "__main__":
    main()
