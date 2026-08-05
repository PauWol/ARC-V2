from pathlib import Path

from arc.foundation.constants import ARC_DIR, get_env, load_dot_env


def test_dot_env_values():
    assert load_dot_env()
    assert ARC_DIR == Path.home() / "arc"


def test_get_env(monkeypatch):
    monkeypatch.setenv("ARC_DIR", "/tmp/test")

    assert get_env("ARC_DIR", "~/arc") == "/tmp/test"
