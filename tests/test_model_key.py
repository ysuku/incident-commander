"""The Claude key is the capstone's own: read from .env as IC_ANTHROPIC_API_KEY,
never from a shell ANTHROPIC_API_KEY, and never copied into os.environ."""
import os

import pytest

from incident_commander import config, model


def test_shell_key_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    (tmp_path / ".env").write_text("OPENAI_MODEL=x\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-shell")
    with pytest.raises(RuntimeError, match="IC_ANTHROPIC_API_KEY"):
        model._capstone_anthropic_key()


def test_key_comes_from_env_file_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    (tmp_path / ".env").write_text("IC_ANTHROPIC_API_KEY=sk-ant-capstone\n")
    monkeypatch.delenv("IC_ANTHROPIC_API_KEY", raising=False)
    assert model._capstone_anthropic_key() == "sk-ant-capstone"
    assert "IC_ANTHROPIC_API_KEY" not in os.environ


def test_settings_load_skips_the_key(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("IC_ANTHROPIC_API_KEY=sk-ant-capstone\nIC_EFFORT=low\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IC_EFFORT", raising=False)
    model._load_env()
    assert os.environ.get("IC_EFFORT") == "low"
    assert "IC_ANTHROPIC_API_KEY" not in os.environ
    monkeypatch.delenv("IC_EFFORT")
