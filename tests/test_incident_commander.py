"""Smoke tests: the package imports and guards fire before data exists."""
import pytest

from incident_commander import config, loaders


def test_config_paths_resolve():
    assert config.REPO_ROOT.name == "capstone" or (config.REPO_ROOT / "pyproject.toml").exists()
    assert config.HELDOUT_SYSTEM != config.DEV_SYSTEM


def test_list_cases_guides_user_when_data_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(loaders, "RCAEVAL_DIR", tmp_path / "nope")
    with pytest.raises(FileNotFoundError, match="download_data"):
        loaders.list_cases("online-boutique")
