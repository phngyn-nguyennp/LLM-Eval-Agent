"""Adapter for ISI213 REFYN plan generation (AI-Mini-Chatbot, mode isi213-refyn-plan).

Generates one plan per benchmark profile, either by calling the repo's pipeline offline
or by calling a deployed server's /chat/completions (e.g. dev), then turns each plan into a two-turn transcript the judge can read:
user = onboarding profile, assistant = title + explain lines + compact sessions.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Bump when the transcript the judge sees changes, so batches judged on different inputs are not compared as equal
INPUT_FORMAT = "refyn-plan/2-labels"

JOINT_LOAD = "src/modes/chatcompletion/refyn_plan/joint_load.json"
# Profile pain -> joints whose load is shown next to each move (mirrors joint_pain_policy in the segment configs)
PAIN_JOINTS = {
    "knees": ["knee"],
    "back": ["spine"],
    "joints": ["knee", "spine", "hip", "ankle", "wrist", "shoulder", "elbow"],
}
LEVEL_SHORT = {"moderate": "mod", "high": "high", "low": "low"}
# Catalog video codes like "-wpl-29-e12" carry nothing for the judge and make long plans longer
VIDEO_CODE = re.compile(r"-(?:wpl|hpl)-\d+-e\d+$")
ROLE_SHORT = {"strength": "str", "mobility": "mob", "static_stretch": "stretch",
              "warmup_light": "wu", "cardio_like": "cardio"}
LABEL_LEGEND = (
    "Labels from the product's exercise catalog, after each move: region; role (str=strength, mob=mobility, "
    "stretch=static stretch, wu=light warm-up, cardio=cardio-like); load on the user's painful joints when "
    "moderate or high; imp=impact when above none; kneel=kneeling contact. Labels can be wrong: "
    "when a label clearly does not fit the move's name, judge the move, not the label."
)

# Runs inside <repo>/src so the repo's own modules (config, settings, models) resolve
GENERATE_SCRIPT = """
import asyncio, inspect, json, sys, traceback
sys.path.insert(0, "../tests/refyn")
from profiles import PROFILES, MALE_PROFILES
from modes.chatcompletion.refyn_plan.pipeline import generate_refyn_plan

only = set(json.loads(sys.argv[2]))
# 1.0.0 takes model=..., 1.1.0 dropped it (selection is code-only)
kwargs = {"model": None} if "model" in inspect.signature(generate_refyn_plan).parameters else {}
results = []
for profile in PROFILES + MALE_PROFILES:
    if only and profile["request_id"] not in only:
        continue
    try:
        plan = asyncio.run(generate_refyn_plan(dict(profile), **kwargs))
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
        items = json.loads(Path(out.name).read_text())
    attach_labels(items, Path(repo_path) / JOINT_LOAD)
    return items


# Prints the benchmark profiles from <repo>/tests/refyn, so a deployed server is asked the same questions
LIST_PROFILES_SCRIPT = """
import json, sys
sys.path.insert(0, "../tests/refyn")
from profiles import PROFILES, MALE_PROFILES
print(json.dumps(PROFILES + MALE_PROFILES))
"""


def fetch_plans(repo_path: str | Path, api_url: str, api_key: str, only: list[str] | None = None,
                concurrency: int = 4) -> list[dict]:
    """Same shape as generate_plans, but each plan comes from POST {api_url}/chat/completions."""
    import httpx
    from concurrent.futures import ThreadPoolExecutor

    proc = subprocess.run([sys.executable, "-c", LIST_PROFILES_SCRIPT], cwd=Path(repo_path) / "src",
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"Could not load benchmark profiles:\n{proc.stderr[-2000:]}")
    profiles = [p for p in json.loads(proc.stdout) if not only or p["request_id"] in only]

    def fetch(profile: dict) -> dict:
        body = {"model": "refyn-plan", "stream": False, "messages": [{"role": "user", "content": "generate plan"}],
                "metadata": {"mode": "isi213-refyn-plan", "config": profile}}
        try:
            resp = httpx.post(f"{api_url.rstrip('/')}/chat/completions", json=body, timeout=120,
                              headers={"Authorization": f"Bearer {api_key}"})
            resp.raise_for_status()
            plan = json.loads(resp.json()["choices"][0]["message"]["content"])
            return {"input": profile, "output": plan}
        except Exception as e:
            return {"input": profile, "output": None, "error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(concurrency) as pool:
        items = list(pool.map(fetch, profiles))
    attach_labels(items, Path(repo_path) / JOINT_LOAD)
    return items


def attach_labels(items: list[dict], joint_load_path: Path) -> None:
    """Copy each move's catalog labels (role, joint load) into the plan; versions before 1.1.0 have none."""
    if not joint_load_path.exists():
        return
    labels = json.loads(joint_load_path.read_text())
    for item in items:
        for session in (item["output"] or {}).get("sessions", []):
            for part in ("warmup", "main", "cooldown"):
                for move in session[part]:
                    if move.get("id") in labels:
                        move["labels"] = labels[move["id"]]


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


def move_text(move: dict, pain_joints: list[str]) -> str:
    tags = [move["region"]]
    labels = move.get("labels")
    if labels:
        tags.append(ROLE_SHORT.get(labels.get("role"), labels.get("role") or "?"))
        if pain_joints:
            tags += [f"{j}:{LEVEL_SHORT[labels[j]]}" for j in pain_joints if labels.get(j) in ("moderate", "high")]
            if labels.get("impact") not in (None, "none"):
                tags.append(f"imp:{LEVEL_SHORT.get(labels['impact'], labels['impact'])}")
            if labels.get("kneeling_contact"):
                tags.append("kneel")
    return f"{VIDEO_CODE.sub('', move['name'])} [{', '.join(tags)}]"


def compact_plan(plan: dict, pain: list[str] | None = None) -> str:
    pain_joints = list(dict.fromkeys(j for p in pain or [] for j in PAIN_JOINTS.get(p, [])))
    labelled = any(m.get("labels") for s in plan["sessions"] for part in ("warmup", "main", "cooldown") for m in s[part])
    lines = [LABEL_LEGEND, ""] if labelled else []
    lines.append(f"Title: {plan.get('title', '')}")
    if plan.get("explain_intro"):  # 1.1.0+: user-facing goal sentence shown above the lines
        lines.append(f"Intro: {plan['explain_intro']}")
    lines.append("Explain:")
    lines += [f"- {line}" for line in plan.get("explain_lines", [])]
    lines.append("")
    for s in plan["sessions"]:
        name = f' "{s["name"]}"' if s.get("name") else ""
        parts = [
            f"{part}: " + ", ".join(move_text(e, pain_joints) for e in s[part])
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
        {"role": "assistant", "content": compact_plan(plan, profile.get("joint_pain"))},
    ]


def profile_label(profile: dict) -> str:
    pain = "+".join(profile.get("joint_pain") or []) or "-"
    return f"{profile['gender'][0]} {profile['age_band']} {profile['focus_area']} {profile['experience']} {pain}"
