from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Status(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    ARCHIVED = "archived"


class Stage(str, Enum):
    """Where a feature version sits in the spec → rubric workflow; *_REVIEW stages wait for a human."""
    SCOPE_REVIEW = "scope_review"      # CP1: AI scope extracted from the PO spec
    SPEC_REVIEW = "spec_review"        # CP2: AI spec written from the approved scope
    RUBRIC_REVIEW = "rubric_review"    # CP3: rubric drafted from the approved AI spec
    APPROVED = "approved"              # rubric approved, eval/run allowed


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass
class DimensionScore:
    name: str
    score: float
    weight: float
    weighted_score: float
    rationale: str = ""
    evidence: str = ""
    run_scores: list[float] = field(default_factory=list)
    misses: list[dict] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)


@dataclass
class AutoFailResult:
    name: str
    description: str
    matched: str = ""


@dataclass
class JudgeResult:
    verdict: Verdict
    overall: float
    pass_threshold: float
    dimensions: list[DimensionScore] = field(default_factory=list)
    auto_fail: bool = False
    auto_fail_reasons: list[AutoFailResult] = field(default_factory=list)
    rubric_version: str = ""


@dataclass
class EvalResult:
    repo: str
    feature: str
    rubric_ver: str
    config_hash: str
    config_name: str
    model: str
    trigger: str
    verdict: Verdict
    overall: float
    dimensions: list[DimensionScore] = field(default_factory=list)
    auto_fail: bool = False
    total_turns: int = 0
    latency_ms: int = 0
    judge_model: str = ""
    user_level: str = ""
    persona: str = ""
    transcript: list[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    judge_prompt_hash: str = ""
    rubric_hash: str = ""
    tool_version: str = ""
    input_format: str = ""  # how the product output was turned into the judge's transcript


@dataclass
class RubricDimension:
    name: str
    weight: float
    description: str
    source_behaviors: list[str] = field(default_factory=list)
    scoring: dict[int, str] = field(default_factory=dict)


@dataclass
class AutoFailRule:
    name: str
    description: str
    pattern: str | None = None
    check: str | None = None
    expected: str | None = None
    threshold: int | None = None


@dataclass
class RubricMeta:
    repo: str
    feature: str
    version: str
    po_spec_doc: str = ""
    ai_spec_doc: str = ""
    generated_at: str = ""
    approved_by: str = ""
    approved_at: str = ""


@dataclass
class Rubric:
    meta: RubricMeta
    dimensions: list[RubricDimension]
    pass_threshold: float = 0.70
    auto_fail: list[AutoFailRule] = field(default_factory=list)


@dataclass
class VersionInfo:
    status: Status = Status.DRAFT
    po_spec_doc: str | None = None
    ai_spec_doc: str | None = None
    eval_report_doc: str | None = None
    created_at: str = ""
    stage: Stage | None = None
    approved_by: str = ""
    approved_at: str = ""
    approvals: list[str] = field(default_factory=list)  # "CP1 <who> <when>" for checkpoints before the rubric


@dataclass
class FeatureInfo:
    adapter: str
    active_version: str | None = None
    api_url: str | None = None
    versions: dict[str, VersionInfo] = field(default_factory=dict)


@dataclass
class RepoInfo:
    path: str
    eval_dir: str = "eval"
    base_url: str = ""
    features: dict[str, FeatureInfo] = field(default_factory=dict)
