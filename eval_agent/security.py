"""`eval-agent security check`: what is exposed, where, and what --fix tightens."""
from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from eval_agent.access import Access, configured_llm_urls
from eval_agent.redact import redact

PRIVATE_FILES = [".env", "eval.db", "access.yaml", "goals.yaml", "registry.yaml"]
PRIVATE_DIRS = ["reports", "calibration", "agent-logs"]
MUST_IGNORE = [".env", "eval.db", "access.yaml", "reports/", "calibration/", "agent-logs/"]
SCAN_SUFFIXES = {".py", ".yaml", ".yml", ".md", ".json", ".csv", ".txt", ".toml", ".cfg", ".ini", ".sh"}
SCAN_SKIP = {".env.example"}


@dataclass
class Finding:
    check: str
    level: str  # ok / warn / fail
    detail: str
    fixed: bool = False


def open_to_others(path: Path) -> bool:
    return bool(path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO))


def check_permissions(home: Path, fix: bool) -> list[Finding]:
    out = []
    targets = [(home / f, 0o600) for f in PRIVATE_FILES] + [(home / d, 0o700) for d in PRIVATE_DIRS]
    for path, mode in targets:
        if not path.exists():
            continue
        paths = [path] + ([p for p in path.rglob("*")] if path.is_dir() else [])
        loose = [p for p in paths if open_to_others(p)]
        if not loose:
            out.append(Finding("permissions", "ok", f"{path.name}: owner only"))
            continue
        if fix:
            for p in loose:
                os.chmod(p, 0o700 if p.is_dir() else 0o600)
        out.append(Finding("permissions", "fail", f"{path.name}: {len(loose)} path(s) readable by group/others", fixed=fix))
    return out


def check_access(home: Path) -> list[Finding]:
    a = Access(home)
    if not a.enabled:
        return [Finding("access", "fail", "no access.yaml: every command is open to anyone who can run the tool "
                                          "(create it with `eval-agent access init`)")]
    out = [Finding("access", "ok", f"owner {a.owner}; " + ", ".join(f"{u}={v['role']}" for u, v in a.users.items()))]
    if a.revoked:
        out.append(Finding("access", "ok", "revoked: " + ", ".join(f"{r['user']} ({r['at']})" for r in a.revoked)))
    still = [r["user"] for r in a.revoked if r["user"] in a.users]
    if still:
        out.append(Finding("access", "warn", f"re-granted after a revocation: {', '.join(still)}"))
    return out


def check_endpoints(home: Path) -> list[Finding]:
    a = Access(home)
    out = []
    for url in configured_llm_urls():
        host = url.split("//")[-1].split("/")[0]
        if not a.enabled:
            out.append(Finding("llm endpoint", "warn", f"{host}: no access.yaml, so not checked"))
        elif a.is_internal(url):
            out.append(Finding("llm endpoint", "ok", f"{host}: internal"))
        elif a.allow_external_llm:
            out.append(Finding("llm endpoint", "warn", f"{host}: external, allowed; transcripts are redacted first"))
        else:
            out.append(Finding("llm endpoint", "fail", f"{host}: external; commands that call it are refused"))
    if os.environ.get("DISCORD_WEBHOOK_URL"):
        out.append(Finding("outbound", "ok", "Discord: only CP4-approved reports, redacted before posting"))
    return out


def git_files(repo: Path) -> list[Path] | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return [repo / l for l in out.stdout.splitlines()] if out.returncode == 0 else None


def check_gitignore(home: Path) -> list[Finding]:
    tracked = git_files(home)
    if tracked is None:
        return []
    names = {p.relative_to(home).as_posix() for p in tracked}
    leaked = [n for n in names if n in (".env", "eval.db", "access.yaml") or n.split("/")[0] in ("reports", "calibration", "agent-logs")]
    if leaked:
        return [Finding("git", "fail", f"private files tracked by git: {', '.join(sorted(leaked)[:5])}")]
    ignore = (home / ".gitignore").read_text().split() if (home / ".gitignore").exists() else []
    missing = [m for m in MUST_IGNORE if m not in ignore and m.rstrip("/") not in ignore]
    if missing:
        return [Finding("git", "warn", f".gitignore does not cover {', '.join(missing)}")]
    return [Finding("git", "ok", "private files are ignored and none is tracked")]


def scan_secrets(paths: list[Path], label: str) -> list[Finding]:
    hits = []
    for p in paths:
        if p.name in SCAN_SKIP or p.suffix not in SCAN_SUFFIXES or not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        _, found = redact(text)
        secret = {k: n for k, n in found.items() if k not in ("email", "phone")}
        if secret:
            hits.append(f"{p.name} ({', '.join(secret)})")
    if hits:
        return [Finding("secrets", "fail", f"{label}: possible secrets in {', '.join(hits[:5])}"
                                           + (f" and {len(hits) - 5} more" if len(hits) > 5 else ""))]
    return [Finding("secrets", "ok", f"{label}: no secrets found")]


def run_checks(home: Path, repo_eval_dirs: list[Path], fix: bool = False) -> list[Finding]:
    findings = check_access(home) + check_permissions(home, fix) + check_gitignore(home) + check_endpoints(home)
    tracked = git_files(home) or []
    findings += scan_secrets(tracked, "tracked code")
    outputs = [p for d in PRIVATE_DIRS if (home / d).exists() for p in (home / d).rglob("*")]
    findings += scan_secrets(outputs, "reports/labelling sheets/agent logs")
    rubric_files = [p for d in repo_eval_dirs if d.exists() for p in d.rglob("*")]
    if rubric_files:
        findings += scan_secrets(rubric_files, "rubrics and specs in product repos")
    return findings
