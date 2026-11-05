"""Mask secrets and personal data in text before it leaves the system (Discord, labelling sheets, external LLMs)."""
from __future__ import annotations

import os
import re
from collections import Counter

SECRET_ENV = ("LLM_API_KEY", "JUDGE_API_KEY", "AGENT_API_KEY", "TARGET_API_KEY", "DISCORD_WEBHOOK_URL")

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("discord_webhook", re.compile(r"https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\S+")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}")),
    ("api_key", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b|\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bxox[abpr]-[A-Za-z0-9-]{10,}\b|\bAKIA[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    # a value with a digit in it: `api_key=api_key` in code is a variable, not a key
    ("key_value", re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password|passwd)\s*[:=]\s*[\"']?(?=[^\s\"',)]*\d)[^\s\"',)]{6,}")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<![\w.])(?:\+84|0)(?:[\s.-]?\d){9,10}(?![\w.])")),
]


def env_secrets() -> list[tuple[str, str]]:
    """Values of the secrets loaded right now, so the exact key is caught whatever its format."""
    return [(k, os.environ[k]) for k in SECRET_ENV if len(os.environ.get(k, "")) >= 6 and os.environ[k] != "not-needed"]


def redact(text: str) -> tuple[str, Counter]:
    """Text with secrets and personal data replaced by [REDACTED:<kind>], and how many of each were found."""
    found: Counter = Counter()
    for name, value in env_secrets():
        if value in text:
            found[name.lower()] += text.count(value)
            text = text.replace(value, f"[REDACTED:{name.lower()}]")
    for kind, pattern in PATTERNS:
        text, n = pattern.subn(f"[REDACTED:{kind}]", text)
        if n:
            found[kind] += n
    return text, found


def redact_transcript(transcript: list[dict]) -> tuple[list[dict], Counter]:
    total: Counter = Counter()
    out = []
    for turn in transcript:
        turn = dict(turn)
        if isinstance(turn.get("content"), str):
            turn["content"], found = redact(turn["content"])
            total += found
        out.append(turn)
    return out, total


def summary(found: Counter) -> str:
    return ", ".join(f"{k}×{n}" for k, n in sorted(found.items())) or "nothing"
