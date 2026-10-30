import pytest

from eval_agent.checks import band_score, compute
from eval_agent.models import RubricDimension

BANDS = {
    0: "More than 30 misplaced exercises",
    2: "21-30 misplaced exercises",
    4: "13-20 misplaced exercises",
    6: "7-12 misplaced exercises",
    8: "3-6 misplaced exercises",
    10: "0-2 misplaced exercises",
}
PART_FIT = RubricDimension(name="session_part_fit", weight=1.2, description="", scoring=BANDS, computed="refyn_part_fit")


@pytest.mark.parametrize("count,score", [(0, 10), (2, 10), (3, 8), (12, 6), (30, 2), (31, 0), (500, 0)])
def test_band_score(count, score):
    assert band_score(BANDS, count) == score


def plan(*sessions: str) -> list[dict]:
    return [{"role": "user", "content": "Profile: ..."},
            {"role": "assistant", "content": "Title: T\nExplain:\n- x\n\n" + "\n".join(sessions)}]


def test_counts_misplaced_moves_by_role():
    transcript = plan(
        # A: plank (str) in warm-up and a stretch in main are misplaced; mobility in cool-down is fine
        'S1 A "Core" 1x rest 20s ~12min | warmup: march [Legs, wu], plank [Core, str] '
        '| main: squat [Legs, str, knee:mod], child-s-pose [Back, stretch] | cooldown: roll-down [Back, mob]',
        # C: stretches in main are expected, a push-up anywhere is not
        'S7 C "Flow" 1x rest 20s ~12min | warmup: cat-cow [Back, mob] '
        '| main: hamstring-stretch [Legs, stretch], push-up [Chest, str] | cooldown: spinal-twist [Back, stretch]',
    )
    result = compute(PART_FIT, transcript)
    assert [m["quote"] for m in result.misses] == ["warmup: plank", "main: child-s-pose", "main: push-up"]
    assert result.score == 8
    assert "out of 9 labelled" in result.rationale


def test_unlabelled_transcript_is_rejected():
    transcript = plan('S1 A "Core" 1x rest 20s ~12min | warmup: march [Legs] | main: squat [Legs] | cooldown: twist [Back]')
    with pytest.raises(ValueError, match="no role labels"):
        compute(PART_FIT, transcript)
