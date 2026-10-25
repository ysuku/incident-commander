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


@needs_data
def test_metric_query_ranks_injected_fault_first():
    c = loaders.load_case("checkoutservice_cpu/1")
    from incident_commander import tools
    top = tools.metric_query(c, top=3)["top_deviations"]
    assert top[0]["series"] == "checkoutservice_cpu"
    assert all(abs(d["z"]) < 1e6 for d in top), "z-scores must stay numerically sane"
    assert all("source_ref" in d for d in top)


@needs_data
def test_log_search_filters_and_cites():
    c = loaders.load_case("checkoutservice_cpu/1")
    from incident_commander import tools
    res = tools.log_search(c, query="timeout", limit=5)
    assert res["total_matches"] >= 0
    for line in res["sample_after_alert"]:
        assert line["source_ref"].startswith("logs.csv#L")
    assert tools.log_search(c, level="error")["total_matches"] == 0  # CPU fault: no error logs


@needs_data
def test_trace_lookup_summarizes_not_dumps():
    c = loaders.load_case("checkoutservice_cpu/1")
    from incident_commander import tools
    res = tools.trace_lookup(c, limit=3)
    assert len(res["traces"]) <= 3
    for t in res["traces"]:
        assert " -> " in t["path"] and t["source_ref"].startswith("traces.csv")
        assert len(t["error_spans"]) <= 5


@needs_data
def test_service_map_has_expected_edges():
    c = loaders.load_case("checkoutservice_cpu/1")
    from incident_commander import tools
    edges = tools.service_map(c)["edges"]
    assert "paymentservice" in edges.get("checkoutservice", [])
    assert "checkoutservice" in edges.get("frontendservice", [])
