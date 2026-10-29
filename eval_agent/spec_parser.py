from __future__ import annotations

import json
from dataclasses import dataclass, field

from openai import AsyncOpenAI

FILTER_PROMPT = """Analyze the following product spec. Separate the requirements into two groups:

**AI Scope** — things the AI/LLM must handle:
- Conversation behavior, flow logic, response generation
- Tone, personality, language constraints
- Error handling, edge cases in AI responses
- Content filtering, safety rules
- Output format/structure the AI must produce

**Not AI Scope** — things handled by other teams:
- UI/UX layout, animations, transitions
- Backend API endpoints, database, infrastructure
- Client-side logic, state management, navigation
- Analytics, tracking events
- Push notifications, payments, auth

For each AI requirement, note:
- MUST: required behavior
- MUST NOT: prohibited behavior
- EDGE: edge case that needs handling

Product Spec:
{spec}

Output as structured markdown with the two groups clearly separated.
Start AI Scope section with "## AI Scope" and Not AI Scope with "## Not AI Scope"."""


AI_SPEC_PROMPT = """Write a precise AI Spec for this feature.
The spec will be used by developers to implement AND by the eval system to judge quality.
Be specific and testable.

**Repo:** {repo}
**Feature:** {feature}

**AI Requirements (filtered from PO spec):**
{ai_scope}

Write the AI Spec with these sections:
1. **Overview** — one paragraph, what the AI does
2. **Behaviors** — numbered list, each is a testable statement
   Format: "B<N>: AI MUST/MUST NOT [specific action] WHEN [condition]"
   Example: "B1: AI MUST correct grammar errors appropriate to the student's CEFR level WHEN the student makes a mistake"
3. **Edge Cases** — how AI handles tricky situations
4. **Constraints** — language, tone, safety, output format

Every statement must be verifiable from a conversation transcript.
Output as markdown."""


RUBRIC_FROM_SPEC_PROMPT = """Convert this AI Spec into evaluation criteria (rubric).
Each testable behavior becomes a dimension.

**Repo:** {repo}
**Feature:** {feature}

**AI Spec:**
{ai_spec}

Generate a YAML rubric with this exact structure:
```yaml
meta:
  repo: {repo}
  feature: {feature}
  version: "{version}"

dimensions:
  - name: dimension_name_snake_case
    weight: 1.0
    source_behaviors: [B1, B2]
    description: >
      What to evaluate
    scoring:
      0: "Worst case description"
      5: "Middle case description"
      10: "Best case description"

pass_threshold: 0.70

auto_fail:
  - name: rule_name
    pattern: "(?i)(regex pattern)"
    description: "What triggers auto-fail"
```

Rules:
1. Group related behaviors into 4-7 dimensions
2. Weight 0.5-1.5 (higher for core requirements)
3. source_behaviors maps to behavior numbers in the AI Spec
4. Every MUST behavior must appear in at least one dimension
5. auto_fail comes from MUST NOT behaviors
6. pass_threshold 0.60-0.80 based on how strict the requirements are

Output ONLY the YAML block, no other text."""


@dataclass
class AIScope:
    ai_items: list[str] = field(default_factory=list)
    non_ai_items: list[str] = field(default_factory=list)
    raw: str = ""

    def to_markdown(self) -> str:
        return self.raw


@dataclass
class AISpec:
    overview: str = ""
    behaviors: list[str] = field(default_factory=list)
    edge_cases: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    raw: str = ""

    def to_markdown(self) -> str:
        return self.raw


async def _call_llm(prompt: str, system: str = "", model: str = "gpt-4o", base_url: str | None = None, api_key: str = "not-needed") -> str:
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
        max_tokens=4096,
    )
    return response.choices[0].message.content or ""


async def parse_po_spec(
    spec_text: str,
    model: str = "gpt-4o",
    base_url: str | None = None,
    api_key: str = "not-needed",
) -> AIScope:
    prompt = FILTER_PROMPT.format(spec=spec_text)
    response = await _call_llm(prompt, model=model, base_url=base_url, api_key=api_key)

    scope = AIScope(raw=response)
    in_ai = False
    in_non_ai = False
    for line in response.split("\n"):
        stripped = line.strip()
        if "## AI Scope" in stripped:
            in_ai = True
            in_non_ai = False
            continue
        if "## Not AI Scope" in stripped:
            in_ai = False
            in_non_ai = True
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            item = stripped[2:]
            if in_ai:
                scope.ai_items.append(item)
            elif in_non_ai:
                scope.non_ai_items.append(item)
    return scope


async def generate_ai_spec(
    ai_scope: AIScope,
    repo: str,
    feature: str,
    model: str = "gpt-4o",
    base_url: str | None = None,
    api_key: str = "not-needed",
) -> AISpec:
    prompt = AI_SPEC_PROMPT.format(
        repo=repo,
        feature=feature,
        ai_scope=ai_scope.to_markdown(),
    )
    response = await _call_llm(prompt, model=model, base_url=base_url, api_key=api_key)

    spec = AISpec(raw=response)
    for line in response.split("\n"):
        stripped = line.strip()
        if stripped.startswith("B") and ": AI " in stripped:
            spec.behaviors.append(stripped)
    return spec


async def generate_rubric_yaml(
    ai_spec: AISpec,
    repo: str,
    feature: str,
    version: str,
    model: str = "gpt-4o",
    base_url: str | None = None,
    api_key: str = "not-needed",
) -> str:
    prompt = RUBRIC_FROM_SPEC_PROMPT.format(
        repo=repo,
        feature=feature,
        version=version,
        ai_spec=ai_spec.to_markdown(),
    )
    response = await _call_llm(prompt, model=model, base_url=base_url, api_key=api_key)

    import re
    yaml_match = re.search(r"```yaml\s*([\s\S]*?)```", response)
    if yaml_match:
        return yaml_match.group(1).strip()
    return response.strip()
