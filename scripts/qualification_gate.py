"""Reject skipped, partial or failed infrastructure qualification JUnit reports."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REQUIRED = {
    "test_actual_generation_registration_reuse_and_next_generation": 1,
    "test_agent_protocols_share_four_node_commit": 1,
    "test_competing_leases_one_stop_partition_quorum_loss_and_recovery": 1,
    "test_sandbox_resource_exhaustion": 4,
    "test_sandbox_network_paths_nonroot_and_checker_protection": 1,
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
