import os

from eval_agent.access import Access
from eval_agent.security import run_checks


def test_loose_permissions_fail_then_fix(isolated_home, monkeypatch):
    for k in ("LLM_BASE_URL", "JUDGE_BASE_URL", "AGENT_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    Access.create(isolated_home, "alice", ["localhost"])
    env = isolated_home / ".env"
    env.write_text("LLM_API_KEY=x\n")
    os.chmod(env, 0o644)
    assert any(f.check == "permissions" and f.level == "fail" for f in run_checks(isolated_home, []))
    run_checks(isolated_home, [], fix=True)
    assert oct(env.stat().st_mode & 0o777) == "0o600"
    assert not any(f.level == "fail" for f in run_checks(isolated_home, []))


def test_missing_access_file_fails(isolated_home):
    assert any(f.check == "access" and f.level == "fail" for f in run_checks(isolated_home, []))


def test_secret_in_reports_is_found(isolated_home):
    Access.create(isolated_home, "alice", ["localhost"])
    (isolated_home / "reports").mkdir(mode=0o700)
    leak = isolated_home / "reports" / "r.md"
    leak.write_text("Authorization: Bearer abcdef1234567890")
    os.chmod(leak, 0o600)
    found = [f for f in run_checks(isolated_home, []) if f.check == "secrets" and f.level == "fail"]
    assert found and "r.md" in found[0].detail
