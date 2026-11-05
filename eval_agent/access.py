"""Who may do what, and where data may go.

`access.yaml` in the working directory lists users and their role. With no access.yaml the tool runs
open (every command allowed), and `security check` reports that as a failure.

Identity is the same name checkpoints are stamped with: EVAL_AGENT_USER, else git user.name, else the
OS user. It is self-declared, so roles guard against mistakes and keep an audit trail; the hard boundary
on a shared machine is the file permissions `security check --fix` sets (owner-only .env, eval.db, ...).
"""
from __future__ import annotations

import getpass
import os
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

from eval_agent.home import get_home

ACCESS_FILE = "access.yaml"
ROLES = ["viewer", "runner", "reviewer", "owner"]

# Command path (as `LoggedGroup` names it) → lowest role allowed. Anything not listed needs "runner".
READ_ONLY = {
    "status", "ops", "batches", "compare", "history", "registry list", "rubric list", "rubric versions",
    "rubric diff", "rubric show", "report list", "report show", "calibrate report", "access list",
    "goal show", "security check",
}
CHECKPOINTS = {  # decisions kept for people (A4): approving, accepting a baseline, sending out, setting goals
    "approve", "rubric approve", "baseline set", "report approve", "report send", "calibrate import", "goal set",
}
ADMIN = {"access grant", "access revoke", "init", "registry add-repo", "registry add-feature", "registry scan",
         "security fix"}
# --by on these names who decided; it must be the person running the command
SIGNED = {"approve", "rubric approve", "report approve"}
# Commands that call an LLM endpoint
LLM_COMMANDS = {"spec parse", "spec generate", "spec pipeline", "approve", "eval", "run", "analyze",
                "calibrate try", "stability", "agent", "auto"}


def current_user() -> str:
    if os.environ.get("EVAL_AGENT_USER"):
        return os.environ["EVAL_AGENT_USER"]
    try:
        git = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=5)
        if git.stdout.strip():
            return git.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return getpass.getuser()


def required_role(command: str, args: list[str]) -> str:
    if command == "baseline":
        command = "baseline set" if "set" in args else "baseline show"
    if command == "security check" and "--fix" in args:
        command = "security fix"
    if command in ADMIN:
        return "owner"
    if command in CHECKPOINTS:
        return "reviewer"
    if command in READ_ONLY or command == "baseline show":
        return "viewer"
    return "runner"


class Access:
    def __init__(self, home: Path | None = None):
        self.path = (home or get_home()) / ACCESS_FILE
        self.enabled = self.path.exists()
        raw = yaml.safe_load(self.path.read_text()) if self.enabled else {}
        raw = raw or {}
        self.owner: str = raw.get("owner", "")
        self.users: dict[str, dict] = raw.get("users") or {}
        self.revoked: list[dict] = raw.get("revoked") or []
        self.internal_hosts: list[str] = raw.get("internal_hosts") or []
        self.allow_external_llm: bool = bool(raw.get("allow_external_llm", False))

    # ─── file ───

    def save(self):
        data = {
            "owner": self.owner,
            "users": self.users,
            "revoked": self.revoked,
            "internal_hosts": self.internal_hosts,
            "allow_external_llm": self.allow_external_llm,
        }
        header = ("# Who may do what (eval-agent access ...). Roles: viewer < runner < reviewer < owner.\n"
                  "# internal_hosts: LLM endpoints that count as inside the company; any other endpoint is\n"
                  "# refused unless allow_external_llm is true, and then transcripts are redacted first.\n")
        self.path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
        os.chmod(self.path, 0o600)
        self.enabled = True

    @classmethod
    def create(cls, home: Path, owner: str, internal_hosts: list[str]) -> "Access":
        a = cls(home)
        a.owner = owner
        a.users = {owner: {"role": "owner", "since": now()}}
        a.internal_hosts = internal_hosts
        a.save()
        return a

    # ─── roles ───

    def role(self, user: str) -> str | None:
        entry = self.users.get(user)
        return entry.get("role") if entry else None

    def revocation(self, user: str) -> dict | None:
        return next((r for r in reversed(self.revoked) if r.get("user") == user), None)

    def check(self, user: str, command: str, args: list[str]) -> tuple[bool, str]:
        """Whether `user` may run `command`; the reason when not."""
        if not self.enabled:
            return True, "no access.yaml (open mode)"
        need = required_role(command, args)
        have = self.role(user)
        if have is None:
            gone = self.revocation(user)
            if gone:
                return False, f"access for {user} was revoked {gone.get('at', '')} by {gone.get('by', '')}"
            return False, f"{user} is not in {ACCESS_FILE} — ask the owner ({self.owner}) to run `eval-agent access grant`"
        if ROLES.index(have) < ROLES.index(need):
            return False, f"`{command}` needs role {need}; {user} is {have}"
        if command in SIGNED:
            by = flag_value(args, "--by")
            if by and by != user:
                return False, f"--by {by} differs from who is running this ({user}); a decision is signed by the person making it"
        return True, ""

    def grant(self, user: str, role: str, by: str):
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        self.users[user] = {"role": role, "since": now(), "granted_by": by}
        self.save()

    def revoke(self, user: str, by: str, reason: str):
        if user == self.owner:
            raise ValueError("the owner cannot be revoked; make someone else owner first")
        if user not in self.users:
            raise ValueError(f"{user} has no access")
        role = self.users.pop(user)["role"]
        self.revoked.append({"user": user, "role": role, "at": now(), "by": by, "reason": reason})
        self.save()

    # ─── where data may go ───

    def is_internal(self, url: str | None) -> bool:
        host = urlparse(url or "").hostname or ""
        return bool(host) and host in self.internal_hosts

    def llm_allowed(self, url: str | None) -> tuple[bool, bool]:
        """(allowed, redact): an internal endpoint gets data as is; an external one only when allowed, redacted."""
        if not self.enabled or self.is_internal(url):
            return True, False
        return self.allow_external_llm, True


def flag_value(args: list[str], flag: str) -> str | None:
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def configured_llm_urls() -> list[str]:
    keys = ("LLM_BASE_URL", "JUDGE_BASE_URL", "AGENT_BASE_URL")
    return [os.environ[k] for k in keys if os.environ.get(k)]


class ExternalEndpointRefused(PermissionError):
    pass


def guard_llm(url: str | None) -> bool:
    """Raise if data may not go to this LLM endpoint; return True when it must be redacted first."""
    allowed, redact = Access().llm_allowed(url)
    if not allowed:
        host = urlparse(url or "").hostname or "(default OpenAI endpoint)"
        raise ExternalEndpointRefused(
            f"LLM endpoint {host} is not in internal_hosts of {ACCESS_FILE}; refusing to send data there. "
            "Add the host if it is internal, or set allow_external_llm: true to send redacted data.")
    return redact
