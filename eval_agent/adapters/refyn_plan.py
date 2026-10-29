"""Adapter for ISI213 REFYN plan generation (AI-Mini-Chatbot, mode isi213-refyn-plan).

Generates one plan per benchmark profile by calling the repo's pipeline offline,
then turns each plan into a two-turn transcript the judge can read:
user = onboarding profile, assistant = title + explain lines + compact sessions.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

# Runs inside <repo>/src so the repo's own modules (config, settings, models) resolve
GENERATE_SCRIPT = """
import asyncio, json, sys, traceback
sys.path.insert(0, "../tests/refyn")
from profiles import PROFILES, MALE_PROFILES
from modes.chatcompletion.refyn_plan.pipeline import generate_refyn_plan

only = set(json.loads(sys.argv[2]))
results = []
for profile in PROFILES + MALE_PROFILES:
    if only and profile["request_id"] not in only:
        continue
    try:
        plan = asyncio.run(generate_refyn_plan(dict(profile), model=None))
        results.append({"input": profile, "output": plan})
    except Exception as e:
        results.append({"input": profile, "output": None, "error": f"{type(e).__name__}: {e}"})
with open(sys.argv[1], "w") as f:
    json.dump(results, f, ensure_ascii=False)
"""


def generate_plans(repo_path: str | Path, only: list[str] | None = None) -> list[dict]:
    """Return [{"input": profile, "output": plan | None, "error"?: str}] for each benchmark profile."""
    src_dir = Path(repo_path) / "src"
    with tempfile.NamedTemporaryFile(suffix=".json") as out:
        proc = subprocess.run(
            [sys.executable, "-c", GENERATE_SCRIPT, out.name, json.dumps(only or [])],
            cwd=src_dir,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Plan generation failed:\n{proc.stderr[-2000:]}")
        return json.loads(Path(out.name).read_text())


def is_light_group(profile: dict) -> bool:
    # Spec §1a: 60+, any joint pain, or 50-59 with experience=never
    return (
        profile["age_band"] == "60+"
        or bool(profile.get("joint_pain"))
        or (profile["age_band"] == "50-59" and profile["experience"] == "never")
    )


def profile_text(profile: dict) -> str:
    pain = " + ".join(profile.get("joint_pain") or []) or "none"
    light = "yes" if is_light_group(profile) else "no"
    return (
        f"Profile: age {profile['age_band']}, {profile['gender']}, "
        f"focus {profile['focus_area']}, experience {profile['experience']}, "
        f"joint pain {pain}, light group {light}"
    )


def compact_plan(plan: dict) -> str:
    lines = [f"Title: {plan.get('title', '')}", "Explain:"]
    lines += [f"- {line}" for line in plan.get("explain_lines", [])]
    lines.append("")
    for s in plan["sessions"]:
        name = f' "{s["name"]}"' if s.get("name") else ""
        parts = [
            f"{part}: " + ", ".join(f"{e['name']} [{e['region']}]" for e in s[part])
            for part in ("warmup", "main", "cooldown")
        ]
        lines.append(
            f"S{s['index']} {s['type']}{name} {s['rounds']}x rest {s['rest_s']}s "
            f"~{s['est_minutes']}min | " + " | ".join(parts)
        )
    return "\n".join(lines)


def to_transcript(profile: dict, plan: dict) -> list[dict]:
    return [
        {"role": "user", "content": profile_text(profile)},
        {"role": "assistant", "content": compact_plan(plan)},
    ]


def profile_label(profile: dict) -> str:
    pain = "+".join(profile.get("joint_pain") or []) or "-"
    return f"{profile['gender'][0]} {profile['age_band']} {profile['focus_area']} {profile['experience']} {pain}"
