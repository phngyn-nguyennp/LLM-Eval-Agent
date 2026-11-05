from click.testing import CliRunner

from eval_agent import cli
from eval_agent.access import Access, guard_llm, ExternalEndpointRefused, required_role
from eval_agent.store import Store

import pytest


def setup_access(home, monkeypatch):
    monkeypatch.setattr(cli, "get_store", lambda: Store(home / "eval.db"))
    a = Access.create(home, "alice", ["llm.internal"])
    a.grant("bob", "runner", by="alice")
    return a


def test_roles_map_checkpoints_to_reviewer():
    assert required_role("baseline", ["set", "--batch", "b"]) == "reviewer"
    assert required_role("baseline", ["show"]) == "viewer"
    assert required_role("report send", []) == "reviewer"
    assert required_role("access revoke", []) == "owner"
    assert required_role("security check", ["--fix"]) == "owner"
    assert required_role("run", []) == "runner"


def test_runner_cannot_pass_a_checkpoint_and_the_refusal_is_logged(isolated_home, monkeypatch):
    setup_access(isolated_home, monkeypatch)
    monkeypatch.setenv("EVAL_AGENT_USER", "bob")
    result = CliRunner().invoke(cli.main, ["baseline", "set", "--batch", "b-1"])
    assert result.exit_code == 1 and "needs role reviewer" in result.output
    log = Store(isolated_home / "eval.db").access_log()
    assert log[0]["user"] == "bob" and log[0]["allowed"] == 0


def test_decision_cannot_be_signed_for_someone_else(isolated_home, monkeypatch):
    setup_access(isolated_home, monkeypatch)
    result = CliRunner().invoke(cli.main, ["report", "approve", "r1", "--by", "carol"])
    assert result.exit_code == 1 and "differs from who is running" in result.output


def test_revoked_user_is_refused(isolated_home, monkeypatch):
    setup_access(isolated_home, monkeypatch)
    assert CliRunner().invoke(cli.main, ["access", "revoke", "bob", "--reason", "left the team"]).exit_code == 0
    monkeypatch.setenv("EVAL_AGENT_USER", "bob")
    result = CliRunner().invoke(cli.main, ["batches", "--repo", "r", "--feature", "f"])
    assert result.exit_code == 1 and "revoked" in result.output
    assert Access(isolated_home).revoked[0]["reason"] == "left the team"


def test_owner_cannot_be_revoked(isolated_home, monkeypatch):
    setup_access(isolated_home, monkeypatch)
    result = CliRunner().invoke(cli.main, ["access", "revoke", "alice", "--reason", "x"])
    assert "cannot be revoked" in result.output and Access(isolated_home).role("alice") == "owner"


def test_external_llm_is_refused_unless_allowed(isolated_home):
    a = Access.create(isolated_home, "alice", ["llm.internal"])
    assert guard_llm("http://llm.internal:8000/v1") is False
    with pytest.raises(ExternalEndpointRefused):
        guard_llm("https://api.example.com/v1")
    a.allow_external_llm = True
    a.save()
    assert guard_llm("https://api.example.com/v1") is True  # allowed, but redacted first


def test_no_access_file_is_open(isolated_home):
    assert Access(isolated_home).check("anyone", "baseline", ["set"]) == (True, "no access.yaml (open mode)")
