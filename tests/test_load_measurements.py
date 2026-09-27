"""Measurement boundaries must not convert missing samples or foreign bytes into evidence."""

import zipfile
from types import SimpleNamespace

import pytest
import test_longevity as load


def test_nearest_rank_percentiles_and_missing_measurements():
    assert load.quantiles([]) is None
    assert load.quantiles([7]) == {"50": 7, "95": 7, "99": 7}
    assert load.quantiles(list(reversed(range(1, 101)))) == {"50": 50, "95": 95, "99": 99}


def test_wheel_identity_rejects_changed_bytes_and_traversal(tmp_path, monkeypatch):
    root = tmp_path / "checkedflow"
    root.mkdir()
    (root / "__init__.py").write_bytes(b"expected")
    monkeypatch.setattr(load, "checkedflow", SimpleNamespace(__file__=str(root / "__init__.py")))
    monkeypatch.setattr(load.sysconfig, "get_path", lambda key: str(tmp_path))

    def wheel(name, body):
        path = tmp_path / "sample.whl"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(name, body)
        return path

    assert (
        load.distribution_identity(wheel("checkedflow/__init__.py", b"expected"))["package_members"]
        == 1
    )
    for name, body in (
        ("checkedflow/__init__.py", b"changed"),
        ("checkedflow/../outside", b"secret"),
    ):
        with pytest.raises(AssertionError):
            load.distribution_identity(wheel(name, body))
    monkeypatch.setattr(load.sysconfig, "get_path", lambda key: str(tmp_path / "another"))
    with pytest.raises(AssertionError):
        load.distribution_identity(wheel("checkedflow/__init__.py", b"expected"))


@pytest.mark.parametrize("mode", ["running", "paused", "draining"])
def test_journal_maintenance_preserves_admission_mode(tmp_path, mode):
    state = SimpleNamespace(
        mode=mode, journal=SimpleNamespace(receipts=[SimpleNamespace(administrative=True)] * 8)
    )
    sent = []
    cluster = SimpleNamespace(
        client=lambda: SimpleNamespace(state=lambda: state),
        send=lambda kind, payload, **kwargs: sent.append(kind),
    )
    measurement = load.Measurement(cluster, tmp_path, {})
    measurement.send("task.finish", {})
    assert sent[:2] == ["mission.pause", "journal.rollover"]
    assert sent[-1] == "task.finish"
    if mode == "running":
        assert sent[2:-1] == ["mission.resume"]
    elif mode == "draining":
        assert sent[2:-1] == ["mission.drain"]
    else:
        assert sent[2:-1] == []


def test_replayed_history_must_contain_independent_checkpoint(tmp_path):
    from test_work_tasks import Harness

    from checkedflow.operational_storage import Store

    harness = Harness()
    _, raw = harness.send("budget.configure", {"budget": 100, "verification_reserve": 20})
    store = Store(tmp_path / "history.sqlite", harness.initial)
    store.commit_block(1, (raw,), previous_hash=load.Runtime(harness.initial).state_hash)
    state = store.load()
    expected = load.Runtime(state).state_hash
    assert load.verify_stored_history(store, (1, expected.upper())) == state
    with pytest.raises(AssertionError):
        load.verify_stored_history(store, (1, "0" * 64))
    # A valid local prefix is not proof of retaining a later committed checkpoint.
    with pytest.raises(AssertionError, match="checkpoint missing"):
        load.verify_stored_history(store, (2, expected))
