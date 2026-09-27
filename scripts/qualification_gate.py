"""Reject skipped, partial or failed infrastructure qualification JUnit reports."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REQUIRED = {
    "test_systemd_comet_custody_stop_and_conflict": 1,
    "test_actual_comet_validator_lock_and_signing_state_restart": 1,
    "test_v2_inherited_accounting_commits_and_recovers_on_four_nodes": 2,
    "test_v2_supervised_effect_report_recovery_with_verified_artifacts": 1,
    "test_v2_effect_reservation_expiry_reconciliation_and_replica_recovery": 1,
    "test_http_mtls_does_not_replace_bearer_or_client_policy": 2,
    "test_actual_grpc_requires_certificate_token_and_current_grant": 1,
    "test_forwarded_headers_cannot_override_transport": 1,
    "test_client_ca_replacement_requires_new_listener_and_rejects_retired_ca": 1,
    "test_systemd_restarts_recovery_after_worker_and_service_sigkill": 1,
    "test_independent_reaper_survives_worker_process_death": 2,
    "test_actual_generation_registration_reuse_and_next_generation": 1,
    "test_agent_protocols_share_four_node_commit": 1,
    "test_competing_leases_one_stop_partition_quorum_loss_and_recovery": 1,
    "test_sandbox_resource_exhaustion": 4,
    "test_sandbox_network_paths_nonroot_and_checker_protection": 1,
    "test_sandbox_nested_repository_tree": 1,
    "test_repository_patch_independent_observation": 6,
    "test_v2_consensus_patch_execution_and_crash_recovery": 1,
    "test_v2_changed_base_requires_fresh_funded_verification": 1,
}


def validate(path: Path) -> None:
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    for name, count in REQUIRED.items():
        matching = [case for case in cases if case.attrib.get("name", "").split("[")[0] == name]
        if len(matching) != count:
            raise ValueError(
                f"qualification case count differs: {name}: {len(matching)} != {count}"
            )
    for case in cases:
        if any(case.find(result) is not None for result in ("skipped", "error", "failure")):
            raise ValueError(f"qualification incomplete: {case.attrib.get('name')}")
    print(f"Infrastructure qualification: {len(cases)} executed and passed")


if __name__ == "__main__":
    validate(Path(sys.argv[1]))
