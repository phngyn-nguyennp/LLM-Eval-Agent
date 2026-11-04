from eval_agent.cli import compare_markdown


def row(item, a, b, same=True, runs=(3, 3)):
    return {"item": item, "a": {"overall": a, "verdict": "PASS" if a >= 0.75 else "FAIL"},
            "b": {"overall": b, "verdict": "PASS" if b >= 0.75 else "FAIL"}, "delta": b - a, "runs": runs,
            "same_output": same, "dims": {"safety": (2.0, 4.0)}}


def test_markdown_flags_noise_and_version_changes():
    rows = [row("bench-01", 0.40, 0.80, same=False), row("bench-02", 0.50, 0.45, runs=(1, 3))]
    versions = [("rubric version", "v1", "v2", True), ("judge model", "m", "m", False)]
    md = compare_markdown("r", "f", "A", "B", versions, ["rubric version"], rows, ["safety"],
                          {"safety": [2.0, 2.0]}, ["bench-02"], ["bench-02"], [], [])
    assert "PASS 0/2 → 1/2" in md
    assert "rubric version v2 (was v1)" in md and "Not like-for-like" in md
    assert "| bench-01 | 0.40→0.80 | +0.40 | FAIL→PASS | 2→4 | 3/3 | changed |" in md
    assert "Same output in both batches (1: bench-02)" in md
    assert "Fewer than 3 judge runs in A or B (bench-02)" in md
