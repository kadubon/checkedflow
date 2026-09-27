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
