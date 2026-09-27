"""Inherited startup must authenticate both approvals before any side effects."""

import sys
from dataclasses import asdict, replace

import pytest
from test_succession import fixture

from checkedflow.core.values import Failure
from checkedflow.distributed import operational_application as app
from checkedflow.wire import dumps


def inputs():
    source, manifest, _ = fixture()
    config = app.Configuration(source["successor"], source["validators"])
    evidence = app.Succession(dumps(manifest), source["legacy"], source["trusted"])
    return config, evidence


def test_startup_requires_exact_approval_before_database_or_socket(tmp_path, monkeypatch):
    config, evidence = inputs()
    called = []
    monkeypatch.setattr(app.grpc, "server", lambda *a, **kw: called.append(True))
    database = tmp_path / "node.db"
    with pytest.raises(Failure, match="BINDING"):
        app.authorize_startup(config.initial, config.validators, None)
    for invalid in (
        None,
        replace(evidence, manifest=b"{}"),
        replace(evidence, trusted=replace(evidence.trusted, state_hash="0" * 64)),
    ):
        with pytest.raises(Failure):
            app.serve(database, config, "127.0.0.1:12345", succession=invalid)
    assert not called and not database.exists()
    app.authorize_startup(config.initial, config.validators, evidence)
    from test_operational_runtime import runtime_and_command

    runtime, _, _ = runtime_and_command()
    fresh = app.Configuration(runtime.state, config.validators)
    app.authorize_startup(fresh.initial, fresh.validators, None)
    with pytest.raises(Failure, match="BINDING"):
        app.authorize_startup(fresh.initial, fresh.validators, evidence)


def test_cli_reads_independent_checkpoint_and_requires_complete_inputs(tmp_path, monkeypatch):
    config, evidence = inputs()
    values = {
        "configuration": dumps(config.encode()),
        "succession-manifest": evidence.manifest,
        "legacy-snapshot": evidence.legacy,
        "legacy-checkpoint": dumps(asdict(evidence.trusted)),
    }
    args = [
        "checkedflow-abci-v2",
        "--database",
        str(tmp_path / "node.db"),
        "--address",
        "127.0.0.1:12345",
    ]
    for name, raw in values.items():
        path = tmp_path / name
        path.write_bytes(raw)
        args.extend(["--" + name, str(path)])
    received = []

    def serve(database, configuration, address, *, succession=None):
        app.authorize_startup(configuration.initial, configuration.validators, succession)
        received.append(succession)

    monkeypatch.setattr(app, "serve", serve)
    monkeypatch.setattr(sys, "argv", args)
    app.main()
    assert received == [evidence]
    monkeypatch.setattr(sys, "argv", args[:-2])
    with pytest.raises(Failure, match="BINDING"):
        app.main()
    assert received == [evidence]
    path = tmp_path / "oversized"
    path.write_bytes(b"x" * 5)
    with pytest.raises(Failure, match="LIMIT"):
        app._read_bounded(path, 4)
