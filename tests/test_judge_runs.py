import asyncio
import json
from types import SimpleNamespace

import pytest

from eval_agent import judge

RUBRIC = """
meta: {version: "v1"}
dimensions:
  - name: clarity
    weight: 1.0
    description: Clear answer
    scoring: {0: bad, 10: good}
"""
TRANSCRIPT = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello there"}]
GOOD = json.dumps({"dimensions": [{"name": "clarity", "score": 8, "rationale": "ok", "misses": []}]})


def fake_client(replies: list[str]):
    """AsyncOpenAI stand-in that hands out `replies` in order, one per chat call."""
    queue = list(replies)

    async def create(**_):
        text = queue.pop(0) if queue else "not json"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    return lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def run_judge(tmp_path, monkeypatch, replies: list[str], runs: int = 3):
    rubric = tmp_path / "v1.yaml"
    rubric.write_text(RUBRIC)
    monkeypatch.setattr(judge, "AsyncOpenAI", fake_client(replies))
    return asyncio.run(judge.judge_transcript(TRANSCRIPT, rubric, runs=runs))


def test_failed_run_is_replaced(tmp_path, monkeypatch):
    # The only run gets three unparseable replies and fails; a replacement run brings the count back to 1
    result = run_judge(tmp_path, monkeypatch, ["junk", "junk", "junk", GOOD], runs=1)
    assert (result.judge_runs, result.judge_runs_wanted) == (1, 1)
    assert len(result.judge_errors) == 1
    assert result.dimensions[0].run_scores == [8.0]


def test_shortfall_is_reported_not_hidden(tmp_path, monkeypatch):
    # One good reply, then nothing parses: the item keeps 1 run and says so once more than `runs` runs have failed
    result = run_judge(tmp_path, monkeypatch, [GOOD])
    assert (result.judge_runs, result.judge_runs_wanted) == (1, 3)
    assert len(result.judge_errors) > 3


def test_all_runs_failing_raises(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="All judge runs failed"):
        run_judge(tmp_path, monkeypatch, [])
