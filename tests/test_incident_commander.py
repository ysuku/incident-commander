"""Loader tests. The data-dependent ones skip when RE2-OB isn't downloaded,
so CI on a fresh clone still passes."""
import pytest

from incident_commander import config, loaders


def test_config_paths_resolve():
    assert (config.REPO_ROOT / "pyproject.toml").exists()
    assert config.HELDOUT_SYSTEM != config.DEV_SYSTEM


def test_missing_data_guides_user(tmp_path, monkeypatch):
    monkeypatch.setattr(loaders, "RCAEVAL_DIR", tmp_path / "nope")
    with pytest.raises(FileNotFoundError, match="download_data"):
        loaders.list_cases("online-boutique")


def test_heldout_system_is_locked():
    with pytest.raises(RuntimeError, match="held-out"):
        loaders.list_cases("train-ticket")


def test_unknown_system_rejected():
    with pytest.raises(ValueError, match="Unknown system"):
        loaders.list_cases("sock-shop")


# --- below need RE2-OB extracted under data/rcaeval/ ---
has_data = (config.RCAEVAL_DIR / "RE2-OB").exists()
needs_data = pytest.mark.skipif(not has_data, reason="RE2-OB not downloaded/extracted")


@needs_data
def test_list_cases_finds_cases_with_ground_truth():
    cases = loaders.list_cases()
    assert cases, "RE2-OB extracted but no cases found"
    c = cases[0]
    assert c.root_cause_service and c.fault_type and c.inject_time > 1_600_000_000
    assert "/" in c.case_id


@needs_data
def test_load_case_roundtrip():
    first = loaders.list_cases()[0]
    again = loaders.load_case(first.case_id)
    assert again == first


@needs_data
def test_load_helpers_return_frames():
    pd = pytest.importorskip("pandas")
    c = loaders.load_case("checkoutservice_cpu/1")
    m = loaders.load_simple_metrics(c)
    assert "time" in m.columns and any(col.endswith("_cpu") for col in m.columns)
    lat, err = loaders.load_trace_timeseries(c)
    assert len(lat) and len(err)
    logs = loaders.load_logs(c, columns=["timestamp", "container_name", "message", "level"])
    assert {"container_name", "message"} <= set(logs.columns)
