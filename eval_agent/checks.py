"""Dimensions scored by code instead of the judge, for rubrics that mark a dimension `computed: <check>`.

Counting hundreds of moves is where the LLM judge breaks down (it snaps to the lowest band),
so a check counts from the transcript itself and maps the count onto the rubric's own bands.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Callable

from eval_agent.models import DimensionScore, RubricDimension

CHECKS: dict[str, Callable[[list[dict], RubricDimension], DimensionScore]] = {}


def check(name: str):
    def register(fn):
        CHECKS[name] = fn
        return fn
    return register


def compute(dim: RubricDimension, transcript: list[dict]) -> DimensionScore:
    if dim.computed not in CHECKS:
        raise ValueError(f"Dimension {dim.name}: unknown check '{dim.computed}' (known: {', '.join(CHECKS)})")
    return CHECKS[dim.computed](transcript, dim)


def band_score(scoring: dict[int, str], count: int) -> float:
    """Score whose description covers `count`: "0-2 …", "21-30 …", "More than 30 …"."""
    for score, desc in scoring.items():
        if m := re.search(r"(\d+)\s*-\s*(\d+)", desc):
            low, high = int(m.group(1)), int(m.group(2))
        elif m := re.search(r"more than (\d+)", desc, re.I):
            low, high = int(m.group(1)) + 1, math.inf
        else:
            continue
        if low <= count <= high:
            return float(score)
    raise ValueError(f"No scoring band covers a count of {count}")


# ─── ISI213 REFYN plan (adapter format refyn-plan/2-labels) ───

SESSION_LINE = re.compile(r"^S(\d+) ([ABC]) ")
MOVE = re.compile(r"([^,\[\]]+?) \[([^\]]*)\]")
HARD = {"str", "cardio"}


@check("refyn_part_fit")
def refyn_part_fit(transcript: list[dict], dim: RubricDimension) -> DimensionScore:
    """Misplaced moves by catalog role: hard moves in warm-up, cool-down or any C session; stretches in A/B main."""
    plan = next(m["content"] for m in transcript if m.get("role") == "assistant")
    turn = next(i for i, m in enumerate(transcript, 1) if m.get("role") == "assistant")
    misses, kinds, labelled, unlabelled = [], Counter(), 0, 0
    for line in plan.splitlines():
        if not (m := SESSION_LINE.match(line)):
            continue
        session, kind = f"S{m.group(1)}", m.group(2)
        for part in line.split(" | ")[1:]:
            phase, _, body = part.partition(": ")
            for name, tags in MOVE.findall(body):
                tags = [t.strip() for t in tags.split(",")]
                if len(tags) < 2:
                    unlabelled += 1
                    continue
                labelled += 1
                role = tags[1]
                if kind == "C" and role in HARD:
                    issue = "hard move in a C session"
                elif phase in ("warmup", "cooldown") and role in HARD:
                    issue = f"{role} move in {phase}"
                elif phase == "main" and kind != "C" and role == "stretch":
                    issue = "static stretch in an A/B main circuit"
                else:
                    continue
                kinds[issue] += 1
                misses.append({"turn": turn, "quote": f"{phase}: {name.strip()}", "issue": f"{session} {issue}"})
    if not labelled:
        raise ValueError(f"{dim.name}: no role labels in the transcript; re-run with an adapter that tags moves")

    score = band_score(dim.scoring, len(misses))
    breakdown = ", ".join(f"{n} {k}" for k, n in kinds.most_common()) or "none"
    rationale = (f"Counted by code from catalog role labels: {len(misses)} misplaced moves ({breakdown}) "
                 f"out of {labelled} labelled" + (f"; {unlabelled} moves had no label and were not counted" if unlabelled else "") + ".")
    return DimensionScore(
        name=dim.name,
        score=score,
        weight=dim.weight,
        weighted_score=score * dim.weight,
        rationale=rationale,
        evidence="; ".join(f'{m["issue"]}: "{m["quote"]}"' for m in misses[:10]),
        misses=misses,
    )
