import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Each test gets an empty working directory, so the real access.yaml, goals and eval.db are never touched."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("EVAL_AGENT_HOME", str(home))
    monkeypatch.setenv("EVAL_AGENT_USER", "alice")
    from eval_agent import cli

    monkeypatch.setattr(cli, "HOME", home)
    return home
