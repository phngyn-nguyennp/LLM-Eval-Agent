from __future__ import annotations

import asyncio
import hashlib
import json
import re
import statistics
from pathlib import Path

from eval_agent.access import guard_llm
from eval_agent.redact import redact_transcript

from openai import AsyncOpenAI

from eval_agent.checks import compute
from eval_agent.models import AutoFailResult, DimensionScore, JudgeResult, Rubric, Verdict
from eval_agent.rubric_loader import load_rubric

JUDGE_SYSTEM_PROMPT = """You are an expert evaluator for AI product outputs: chatbot conversations,
or generated artifacts (plans, content) shown as a user input turn and an
assistant output turn.
You will receive a transcript and a set of evaluation dimensions.
Score each dimension from 0 to 10 based on the scoring guide provided.

Be strict and evidence-based. For each dimension:
- List every problem you penalise in "misses". Each miss cites the turn number
  shown in the transcript and a VERBATIM quote copied from that turn. Never
  paraphrase inside "quote"; never describe what a turn contains without
  checking it word for word. Keep each quote to the shortest verbatim span
  that shows the problem (at most ~15 words), and list at most 5 misses per
  dimension, the most serious first.
- Always return every dimension listed, even when it has no misses.
- A dimension with no misses should score high.
- Explain your reasoning in "rationale" and use the full 0-10 range.

Respond in JSON format:
{
  "dimensions": [
    {
      "name": "dimension_name",
      "score": 7,
      "rationale": "Brief explanation",
      "misses": [
        {"turn": 10, "quote": "exact words from turn 10", "issue": "what is wrong"}
      ]
    }
  ]
}"""


def judge_prompt_hash() -> str:
    """Version of the judge's instructions, stored with every run so score changes can be traced to the judge."""
    return hashlib.sha256(JUDGE_SYSTEM_PROMPT.encode()).hexdigest()[:12]


def file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def _normalize(text: str) -> str:
    text = re.sub(r"[*_`]", "", text.lower())
    text = re.sub(r"[^\w\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def verify_misses(conversation: list[dict], scores: list[DimensionScore]) -> list[str]:
    """Return a description of every miss whose quote is not in the cited turn (±1)."""
    turns = [_normalize(m["content"]) for m in conversation]
    bad = []
    for dim in scores:
        for miss in dim.misses:
            quote = _normalize(str(miss.get("quote", "")))
            turn = miss.get("turn")
            if not quote or not isinstance(turn, int):
                bad.append(f"{dim.name}: miss without turn/quote")
                continue
            window = turns[max(0, turn - 2): turn + 1]
            if not any(quote in t for t in window):
                bad.append(f'{dim.name}: turn {turn} does not contain "{miss.get("quote")}"')
    return bad


CONVERSATION_ROLES = ("user", "assistant")


def conversation_only(transcript: list[dict]) -> list[dict]:
    return [m for m in transcript if m.get("role") in CONVERSATION_ROLES and m.get("content")]


def format_transcript(transcript: list[dict]) -> str:
    lines = []
    for i, msg in enumerate(conversation_only(transcript)):
        role = msg["role"].upper()
        lines.append(f"[Turn {i+1}] {role}: {msg['content']}")
    return "\n\n".join(lines)


def build_judge_prompt(
    transcript: list[dict],
    dimensions: list,
    config: dict | None = None,
    user_level: str | None = None,
) -> str:
    parts = []
    parts.append("## Transcript\n")
    parts.append(format_transcript(transcript))
    parts.append("\n\n## Evaluation Dimensions\n")
    for dim in dimensions:
        parts.append(f"### {dim.name} (weight: {dim.weight})")
        parts.append(f"Description: {dim.description}")
        if dim.scoring:
            parts.append("Scoring guide:")
            for score, desc in sorted(dim.scoring.items()):
                parts.append(f"  {score}: {desc}")
        parts.append("")
    if config:
        parts.append(f"\n## Flow Config\n```json\n{json.dumps(config, indent=2, ensure_ascii=False)}\n```")
    if user_level:
        parts.append(f"\n## User Level: {user_level}")
    parts.append("\nScore each dimension 0-10. Respond in the JSON format specified.")
    return "\n".join(parts)


def check_auto_fails(transcript: list[dict], rules: list) -> list[AutoFailResult]:
    results = []
    assistant_msgs = [m["content"] for m in transcript if m.get("role") == "assistant" and m.get("content")]

    for rule in rules:
        if rule.pattern:
            pattern = re.compile(rule.pattern)
            for msg in assistant_msgs:
                match = pattern.search(msg)
                if match:
                    results.append(AutoFailResult(
                        name=rule.name,
                        description=rule.description,
                        matched=match.group(),
                    ))
                    break

        elif rule.check == "repeated_response":
            threshold = rule.threshold or 3
            from collections import Counter
            counts = Counter(assistant_msgs)
            for msg, count in counts.items():
                if count >= threshold:
                    results.append(AutoFailResult(
                        name=rule.name,
                        description=rule.description,
                        matched=f"Repeated {count} times: {msg[:100]}...",
                    ))
                    break

        elif rule.check == "session_not_complete":
            last_assistant = next(
                (m["content"] for m in reversed(transcript) if m.get("role") == "assistant"),
                "",
            )
            completion_markers = [
                "lesson complete", "great job", "well done",
                "session complete", "all missions done", "great discussion",
            ]
            if not any(marker in last_assistant.lower() for marker in completion_markers):
                results.append(AutoFailResult(
                    name=rule.name,
                    description=rule.description,
                    matched="No completion marker found in last assistant message",
                ))

    return results


def parse_judge_response(response_text: str, dimensions: list) -> list[DimensionScore]:
    json_match = re.search(r"\{[\s\S]*\}", response_text)
    if not json_match:
        raise ValueError("No JSON found in judge response")

    data = json.loads(json_match.group())
    scored = data.get("dimensions", [])
    dim_map = {d["name"]: d for d in scored if "name" in d}
    missing = [dim.name for dim in dimensions if dim.name not in dim_map or "score" not in dim_map[dim.name]]
    if missing:
        raise ValueError(f"Judge response missing dimensions: {missing}")

    results = []
    for dim in dimensions:
        scored_dim = dim_map.get(dim.name, {})
        score = float(scored_dim.get("score", 0))
        results.append(DimensionScore(
            name=dim.name,
            score=score,
            weight=dim.weight,
            weighted_score=score * dim.weight,
            rationale=scored_dim.get("rationale", ""),
            evidence="; ".join(f'Turn {m.get("turn")}: "{m.get("quote")}"' for m in scored_dim.get("misses", []) if isinstance(m, dict)),
            misses=[m for m in scored_dim.get("misses", []) if isinstance(m, dict)],
        ))
    return results


def sampling_params(model: str) -> dict:
    # Reasoning models reject temperature and count hidden reasoning against the token budget.
    if re.match(r"^(gpt-5|o\d)", model):
        return {"max_completion_tokens": 16000}
    return {"temperature": 0.1, "max_tokens": 8192}


def median_scores(all_runs: list[list[DimensionScore]]) -> list[DimensionScore]:
    result = []
    for i, first in enumerate(all_runs[0]):
        per_run = [run[i] for run in all_runs]
        med = statistics.median(s.score for s in per_run)
        closest = min(per_run, key=lambda s: abs(s.score - med))
        result.append(DimensionScore(
            name=first.name,
            score=med,
            weight=first.weight,
            weighted_score=med * first.weight,
            rationale=closest.rationale,
            evidence=closest.evidence,
            run_scores=[s.score for s in per_run],
            misses=closest.misses,
            unverified=closest.unverified,
        ))
    return result


async def judge_transcript(
    transcript: list[dict],
    rubric_path: str | Path,
    config: dict | None = None,
    user_level: str | None = None,
    model: str = "apero-llm",
    base_url: str | None = None,
    api_key: str = "not-needed",
    runs: int = 3,
) -> JudgeResult:
    rubric = load_rubric(rubric_path)

    auto_fails = check_auto_fails(transcript, rubric.auto_fail)
    if auto_fails:
        return JudgeResult(
            verdict=Verdict.FAIL,
            overall=0.0,
            pass_threshold=rubric.pass_threshold,
            auto_fail=True,
            auto_fail_reasons=auto_fails,
            rubric_version=rubric.meta.version,
        )

    # Dimensions the rubric hands to code are scored up front, so a missing label fails before any LLM call
    computed = {d.name: compute(d, transcript) for d in rubric.dimensions if d.computed}
    llm_dims = [d for d in rubric.dimensions if not d.computed]

    # Past this point the transcript goes to the LLM: refused for a non-internal endpoint, redacted when allowed
    if guard_llm(base_url):
        transcript = redact_transcript(transcript)[0]

    prompt = build_judge_prompt(
        transcript=transcript,
        dimensions=llm_dims,
        config=config,
        user_level=user_level,
    )

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    conversation = conversation_only(transcript)

    async def judge_once(attempts: int = 3) -> list[DimensionScore]:
        messages = [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        scores: list[DimensionScore] = []
        bad: list[str] = []
        for attempt in range(attempts):
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                **sampling_params(model),
            )
            text = response.choices[0].message.content or ""
            try:
                scores = parse_judge_response(text, llm_dims)
            except (ValueError, json.JSONDecodeError):
                if attempt == attempts - 1 and not scores:
                    raise
                continue
            bad = verify_misses(conversation, scores)
            if not bad:
                return scores
            messages = messages[:2] + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": (
                    "These cited quotes do NOT appear in the cited turns of the transcript:\n- "
                    + "\n- ".join(bad)
                    + "\nRe-read those turns. Drop or fix any miss you cannot back with a verbatim "
                    "quote, re-score the affected dimensions, and return the full JSON again."
                )},
            ]
        for dim in scores:
            dim.unverified = [b for b in bad if b.startswith(dim.name + ":")]
        return scores

    wanted = max(1, runs)
    errors: list[str] = []
    if llm_dims:
        # A run that fails (no parseable JSON after its retries, API error) is replaced, so the median is over
        # `runs` runs as asked; after more than `wanted` failures the item keeps what it has and reports the shortfall
        all_runs: list[list[DimensionScore]] = []
        while len(all_runs) < wanted and len(errors) <= wanted:
            outcomes = await asyncio.gather(*[judge_once() for _ in range(wanted - len(all_runs))],
                                            return_exceptions=True)
            all_runs += [o for o in outcomes if not isinstance(o, BaseException)]
            errors += [f"{type(o).__name__}: {o}"[:200] for o in outcomes if isinstance(o, BaseException)]
        if not all_runs:
            raise RuntimeError(f"All judge runs failed: {'; '.join(errors)}")
        judged = {s.name: s for s in median_scores(all_runs)}
        n_runs = len(all_runs)
    else:
        judged, n_runs = {}, wanted
    for s in computed.values():
        s.run_scores = [s.score] * n_runs  # deterministic: same score every run, so stability reads it as zero noise
    scores = [judged.get(d.name) or computed[d.name] for d in rubric.dimensions]

    weighted_sum = sum(s.weighted_score for s in scores)
    max_weighted = sum(10.0 * d.weight for d in rubric.dimensions)
    overall = weighted_sum / max_weighted if max_weighted > 0 else 0.0

    return JudgeResult(
        verdict=Verdict.PASS if overall >= rubric.pass_threshold else Verdict.FAIL,
        overall=round(overall, 4),
        pass_threshold=rubric.pass_threshold,
        dimensions=scores,
        rubric_version=rubric.meta.version,
        judge_runs=n_runs,
        judge_runs_wanted=wanted,
        judge_errors=errors,
    )
