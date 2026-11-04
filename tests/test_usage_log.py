from click.testing import CliRunner

from eval_agent import cli
from eval_agent.store import Store


def test_commands_are_logged_without_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "get_store", lambda: Store(tmp_path / "eval.db"))
    runner = CliRunner()
    runner.invoke(cli.main, ["batches", "--repo", "r", "--feature", "f"])
    runner.invoke(cli.main, ["batches", "--help"])
    runner.invoke(cli.main, ["no-such-command"])
    rows = Store(tmp_path / "eval.db").conn.execute("SELECT command, ok FROM usage_log").fetchall()
    assert [tuple(r) for r in rows] == [("batches", 1)]


def test_group_command_is_logged_with_its_subcommand(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "get_store", lambda: Store(tmp_path / "eval.db"))
    CliRunner().invoke(cli.main, ["rubric", "versions", "--repo", "r", "--feature", "f"])
    commands = [r[0] for r in Store(tmp_path / "eval.db").conn.execute("SELECT command FROM usage_log")]
    assert commands == ["rubric versions"]
