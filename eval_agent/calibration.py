"""How far the LLM judge can be trusted: agreement with human labels, and stability across repeated runs."""
from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path

from eval_agent.rubric_loader import load_rubric

# A dimension is trusted when the judge lands within ±2 of the human on most items
# and ranks items the same way; below these it should not drive decisions on its own.
TRUST_WITHIN2 = 0.70
TRUST_SPEARMAN = 0.50
TRUST_MAE = 2.0
UNSTABLE_RANGE = 3  # max-min across repeated judge runs on the same transcript


def _ranks(values: list[float]) -> list[float]:
    """Average ranks, so tied scores (common on a 0-10 scale) share a rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation; None when undefined (fewer than 3 items or no spread)."""
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy) ** 0.5


def agreement(judge: list[float], human: list[float]) -> dict:
    diffs = [j - h for j, h in zip(judge, human)]
    rho = spearman(judge, human)
    stats = {
        "n": len(diffs),
        "mae": statistics.mean(abs(d) for d in diffs),
        "within2": sum(abs(d) <= 2 for d in diffs) / len(diffs),
        "bias": statistics.mean(diffs),  # > 0: judge scores higher than humans
        "spearman": rho,
    }
    # With no spread in human scores rank agreement is undefined, so judge on distance only
    stats["trusted"] = (
        stats["within2"] >= TRUST_WITHIN2
        and stats["mae"] <= TRUST_MAE
        and (rho is None or rho >= TRUST_SPEARMAN)
    )
    return stats


# ─── Human labelling sheet ───


def export_sheet(out_dir: Path, runs: list[dict], rubric_path: Path) -> Path:
    """Write one readable file per item plus a CSV to fill in. Judge scores are left out on purpose."""
    rubric = load_rubric(rubric_path)
    items_dir = out_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)

    guide = [f"# Scoring guide — {rubric.meta.repo} / {rubric.meta.feature} {rubric.meta.version}", "",
             "Score each item 0-10 per dimension in labels.csv, without looking at the judge's scores.", ""]
    for d in rubric.dimensions:
        guide += [f"## {d.name}", "", d.description.strip(), ""]
        guide += [f"- **{score}**: {desc}" for score, desc in sorted(d.scoring.items())] + [""]
    (out_dir / "GUIDE.md").write_text("\n".join(guide))

    sheet = out_dir / "labels.csv"
    with sheet.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["item", "run_id", "dimension", "human_score", "note"])
        for run in runs:
            item = run["config_name"] or run["id"][:8]
            turns = json.loads(run["transcript"] or "[]")
            body = [f"# {item}", ""]
            for i, turn in enumerate(turns, 1):
                body += [f"## Turn {i} · {turn.get('role', '').upper()}", "", str(turn.get("content", "")), ""]
            (items_dir / f"{item}.md").write_text("\n".join(body))
            for d in rubric.dimensions:
                writer.writerow([item, run["id"], d.name, "", ""])
    return sheet


def read_sheet(path: Path) -> tuple[list[dict], list[str]]:
    """Filled rows from a labelling sheet, plus problems found (bad or out-of-range scores)."""
    rows, problems = [], []
    with path.open(newline="") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            raw = (row.get("human_score") or "").strip()
            if not raw:
                continue
            try:
                score = float(raw)
            except ValueError:
                problems.append(f"line {line_no}: '{raw}' is not a number")
                continue
            if not 0 <= score <= 10:
                problems.append(f"line {line_no}: {score} is outside 0-10")
                continue
            rows.append({"run_id": row["run_id"], "dimension": row["dimension"], "score": score,
                         "note": (row.get("note") or "").strip()})
    return rows, problems


# ─── Stability across repeated judge runs ───


def stability(per_item: dict[str, dict[str, list[float]]], weights: dict[str, float], threshold: float) -> dict:
    """per_item: item -> dimension -> scores from repeated runs on the same transcript."""
    dims = sorted({d for scores in per_item.values() for d in scores})
    by_dim = {}
    for d in dims:
        series = [s[d] for s in per_item.values() if d in s and len(s[d]) > 1]
        ranges = [max(x) - min(x) for x in series]
        by_dim[d] = {
            "n": len(series),
            "mean_std": statistics.mean(statistics.pstdev(x) for x in series) if series else 0.0,
            "mean_range": statistics.mean(ranges) if ranges else 0.0,
            "unstable_share": sum(r >= UNSTABLE_RANGE for r in ranges) / len(ranges) if ranges else 0.0,
        }

    flips = []
    max_weighted = sum(10.0 * w for w in weights.values())
    for item, scores in per_item.items():
        runs = min(len(v) for v in scores.values())
        verdicts = set()
        for k in range(runs):
            overall = sum(scores[d][k] * weights.get(d, 1.0) for d in scores) / max_weighted
            verdicts.add(overall >= threshold)
        if len(verdicts) > 1:
            flips.append(item)
    return {"dimensions": by_dim, "verdict_flips": flips, "items": len(per_item)}
