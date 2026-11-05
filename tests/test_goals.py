from eval_agent.goals import Goal, check_goal


def runs(*scores):
    return [{"verdict": "PASS" if s >= 0.75 else "FAIL", "overall": s, "config_name": f"i{n}"} for n, s in enumerate(scores)]


def test_goal_met():
    r = check_goal(Goal(min_pass_rate=0.5, min_mean=0.6), runs(0.8, 0.5), [], {"i0": 3, "i1": 3})
    assert r.met and all(l.startswith("✅") for l in r.lines)


def test_goal_not_met_says_why():
    r = check_goal(Goal(min_pass_rate=1.0, max_regressions=0), runs(0.8, 0.5), ["i1"], {"i0": 3, "i1": 1})
    assert not r.met
    failed = [l for l in r.lines if l.startswith("❌")]
    assert len(failed) == 3 and any("i1" in l for l in failed)
