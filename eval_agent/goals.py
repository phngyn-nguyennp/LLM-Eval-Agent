"""Goals a person sets for a feature, which `eval-agent auto` works toward without crossing checkpoints."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from eval_agent.home import get_home

GOALS_FILE = "goals.yaml"


@dataclass
class Goal:
    min_pass_rate: float = 1.0      # share of items that must PASS
    min_mean: float = 0.0           # mean overall score
    max_regressions: int = 0        # items allowed below their baseline by more than the regression threshold
    min_judge_runs: int = 3         # items judged fewer times than this make the result unreliable
    note: str = ""
    set_by: str = ""
    set_at: str = ""


@dataclass
class GoalCheck:
    met: bool
    lines: list[str] = field(default_factory=list)


def load_goals(home: Path | None = None) -> dict[str, Goal]:
    path = (home or get_home()) / GOALS_FILE
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    return {k: Goal(**v) for k, v in (raw or {}).items()}


def save_goals(goals: dict[str, Goal], home: Path | None = None):
    path = (home or get_home()) / GOALS_FILE
    path.write_text("# Set with `eval-agent goal set` (a checkpoint); read by `eval-agent auto`.\n"
                    + yaml.safe_dump({k: asdict(g) for k, g in goals.items()}, sort_keys=False, allow_unicode=True))


def key(repo: str, feature: str) -> str:
    return f"{repo}/{feature}"


def check_goal(goal: Goal, runs: list[dict], regressions: list[str], judge_runs: dict[str, int]) -> GoalCheck:
    """Compare one saved batch with the goal. `runs` are store rows; `judge_runs` maps item → runs it got."""
    n = len(runs)
    if not n:
        return GoalCheck(False, ["no saved items"])
    passed = sum(r["verdict"] == "PASS" for r in runs)
    mean = sum(r["overall"] for r in runs) / n
    short = sorted(i for i, k in judge_runs.items() if k < goal.min_judge_runs)
    checks = [
        (passed / n >= goal.min_pass_rate, f"PASS {passed}/{n} ({passed / n:.0%}), goal ≥ {goal.min_pass_rate:.0%}"),
        (mean >= goal.min_mean, f"mean overall {mean:.2f}, goal ≥ {goal.min_mean:.2f}"),
        (len(regressions) <= goal.max_regressions,
         f"regressions vs baseline {len(regressions)}{' (' + ', '.join(regressions) + ')' if regressions else ''}, "
         f"goal ≤ {goal.max_regressions}"),
        (not short, f"items judged fewer than {goal.min_judge_runs} times: {', '.join(short) or 'none'}"),
    ]
    return GoalCheck(all(ok for ok, _ in checks), [("✅ " if ok else "❌ ") + text for ok, text in checks])
