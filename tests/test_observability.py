"""Readiness fails closed while metrics preserve the distinction from process liveness."""

from dataclasses import replace
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator
from starlette.applications import Starlette
from starlette.testclient import TestClient
from test_agent_access import grant, save
from test_agents import TOKEN
from test_dispatch_watchdog import Fixture
from test_operational_gateway import Node

from checkedflow.agents.a2a import create_app
from checkedflow.agents.access import LOCAL, Policy
from checkedflow.agents.http import Guard
from checkedflow.agents.mcp import create_http_app
from checkedflow.core.values import Failure
from checkedflow.observability import REQUIRED, Monitor, gauges
from checkedflow.operational_codec import decode, encode
from checkedflow.wire import document, dumps


def fixture(role="gateway"):
    f = Fixture()
    return f, Monitor(f.watchdog, role, {name: lambda: True for name in REQUIRED[role]})


def test_cold_stalled_and_paused_services_are_live_but_not_dispatch_ready():
    f, monitor = fixture()
    cold = monitor.observe()
    assert cold.readable and not cold.ready and cold.record()["live"] is True
    f.tick(2, 101)
    assert monitor.observe().ready
    f.now = 122
    assert not monitor.observe().ready
    f.tick(3, 123)
    assert monitor.observe().ready
    f.state = replace(f.state, height=4, mode="paused")
    paused = monitor.observe()
    assert paused.readable and not paused.ready
    assert dict(paused.values)["mission_paused"] == 1


def test_failed_observation_never_exports_old_height_or_error_details():
    f, monitor = fixture()
    f.ready()
    assert monitor.observe().ready
    f.error = OSError("private path token and endpoint")
    failed = monitor.observe()
    assert not failed.readable and not failed.ready and not failed.values
    assert "committed_height" not in failed.prometheus()
    assert "private" not in str(failed.record())
    assert "checkedflow_process_live 1\n" in failed.prometheus()


@pytest.mark.parametrize("role", sorted(REQUIRED))
def test_role_dependencies_are_complete_strict_and_rechecked(role):
    f = Fixture()
    f.ready()
    healthy = True
    probes = {name: lambda: healthy for name in REQUIRED[role]}
    monitor = Monitor(f.watchdog, role, probes)
    assert monitor.observe().ready
    healthy = False
    assert not monitor.observe().ready
    with pytest.raises(Failure, match="CONFIGURATION"):
        Monitor(f.watchdog, role, {})
    with pytest.raises(Failure, match="CONFIGURATION"):
        Monitor(f.watchdog, role, probes | {"arbitrary-endpoint": lambda: True})


@pytest.mark.parametrize("value", [False, None, 1, "true"])
def test_truthy_probe_values_do_not_assert_readiness(value):
    f = Fixture()
    f.ready()
    monitor = Monitor(
        f.watchdog, "gateway", {"configuration": lambda: True, "storage": lambda: value}
    )
    assert not monitor.observe().ready


def test_slow_dependency_probe_cannot_extend_node_freshness():
    f = Fixture()
    f.ready()

    def slow():
        f.now += 10
        return True

    monitor = Monitor(f.watchdog, "gateway", {"configuration": lambda: True, "storage": slow})
    assert not monitor.observe().ready


def test_probe_failure_and_interrupt_are_distinct():
    f = Fixture()
    f.ready()
    error = OSError("private")

    def probe():
        raise error

    monitor = Monitor(f.watchdog, "gateway", {"configuration": lambda: True, "storage": probe})
    assert not monitor.observe().ready and "private" not in monitor.observe().prometheus()
    error = SystemExit()
    with pytest.raises(SystemExit):
        monitor.observe()
    with pytest.raises(Failure, match="SHAPE"):
        Monitor(f.watchdog, "arbitrary-role", {})
    with pytest.raises(Failure, match="CONFIGURATION"):
        Monitor(f.watchdog, "gateway", {"configuration": None, "storage": probe})


def test_committed_gauges_survive_replay_without_identifiers_or_false_counters(
    tmp_path, monkeypatch
):
    from test_effect_dispatch import fixture as effect_fixture

    f = effect_fixture(tmp_path, monkeypatch)
    state = f.h.runtime.state
    result = gauges(state)
    assert result["effects_dispatch_reserved"] == 1 and result["budget_spent_units"] == 40
    assert result == gauges(decode(dumps(encode(state)))) == gauges(state)
    assert all(not name.endswith("_total") for name in result)
    assert f.h.current.operation not in str(result) and state.chain not in str(result)
    with pytest.raises(Failure, match="VERSION"):
        gauges(replace(state, profile="future"))


@pytest.mark.parametrize("transport", ["a2a", "mcp"])
def test_actual_transports_keep_monitoring_authenticated_and_policy_bound(tmp_path, transport):
    node = Node()
    f, monitor = fixture()
    path = tmp_path / "access.json"
    save(path, [grant()])
    policy = Policy(path, f.state.chain, f.state.mission)
    app = (
        create_app(node.gateway(), "http://127.0.0.1/rpc", TOKEN, policy=policy, monitor=monitor)
        if transport == "a2a"
        else create_http_app(node.gateway(), TOKEN, policy=policy, monitor=monitor)
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for route in ("/healthz", "/readyz", "/metrics"):
            assert client.get(route).status_code == 401
        client.headers["Authorization"] = "Bearer " + TOKEN
        assert client.get("/healthz").json() == {"live": True, "role": "gateway"}
        assert client.get("/readyz").status_code == 503
        f.tick(2, 101)
        assert client.get("/readyz").status_code == 200
        metrics = client.get("/metrics")
        assert metrics.status_code == 200 and "checkedflow_protected_work_ready 1\n" in metrics.text
        assert client.post("/healthz").status_code == 405
        save(path, [])
        assert client.get("/metrics").status_code == 403


def test_monitoring_cannot_be_public_or_bound_to_another_policy(tmp_path):
    f, monitor = fixture()
    path = tmp_path / "access.json"
    save(path, [grant()])
    policy = Policy(path, f.state.chain, f.state.mission)
    for options in (
        {},
        {"policy": policy, "authenticate": lambda _: LOCAL, "public": ("/metrics",)},
        {"policy": Policy(path, "another", "m"), "authenticate": lambda _: LOCAL},
    ):
        with pytest.raises(Failure, match="ACCESS|SCOPE"):
            Guard(Starlette(), TOKEN, monitor=monitor, **options)


def test_policy_revocation_during_probe_blocks_response_egress(tmp_path):
    f = Fixture()
    f.ready()
    path = tmp_path / "access.json"
    save(path, [grant()])
    policy = Policy(path, f.state.chain, f.state.mission)

    def revoked():
        save(path, [])
        return True

    monitor = Monitor(f.watchdog, "gateway", {"configuration": lambda: True, "storage": revoked})
    app = Guard(Starlette(), TOKEN, authenticate=lambda _: LOCAL, policy=policy, monitor=monitor)
    with TestClient(app, headers={"Authorization": "Bearer " + TOKEN}) as client:
        result = client.get("/readyz")
        assert result.status_code == 403 and "gauges" not in result.text


def test_packaged_observation_schema_and_bounded_prometheus_format():
    schema = document(
        files("checkedflow").joinpath("data/service-observation.schema.json").read_bytes()
    )
    validator = Draft202012Validator(schema)
    for role in REQUIRED:
        f, monitor = fixture(role)
        f.ready()
        record = monitor.observe().record()
        validator.validate(record)
        assert list(validator.iter_errors(record | {"role": "future"}))
        assert list(validator.iter_errors(record | {"checks": {}}))
        metric_lines = monitor.observe().prometheus().splitlines()
        assert len(metric_lines) < 128
        seen = set()
        for header, sample in zip(metric_lines[::2], metric_lines[1::2], strict=True):
            name, value = sample.split()
            assert name not in seen and header == f"# TYPE {name} gauge"
            assert name.startswith("checkedflow_") and value.isdecimal()
            seen.add(name)
