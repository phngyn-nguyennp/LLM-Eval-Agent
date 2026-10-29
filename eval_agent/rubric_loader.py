from __future__ import annotations

from pathlib import Path

import yaml

from eval_agent.models import AutoFailRule, Rubric, RubricDimension, RubricMeta


def load_rubric(path: str | Path) -> Rubric:
    raw = yaml.safe_load(Path(path).read_text())
    meta_raw = raw.get("meta", {})
    meta = RubricMeta(
        repo=meta_raw.get("repo", ""),
        feature=meta_raw.get("feature", ""),
        version=str(meta_raw.get("version", "")),
        po_spec_doc=meta_raw.get("po_spec_doc", ""),
        ai_spec_doc=meta_raw.get("ai_spec_doc", ""),
        generated_at=meta_raw.get("generated_at", ""),
        approved_by=meta_raw.get("approved_by", ""),
        approved_at=meta_raw.get("approved_at", ""),
    )

    dimensions = []
    for d in raw.get("dimensions", []):
        dimensions.append(RubricDimension(
            name=d["name"],
            weight=float(d.get("weight", 1.0)),
            description=d.get("description", ""),
            source_behaviors=d.get("source_behaviors", []),
            scoring=d.get("scoring", {}),
        ))

    auto_fail_rules = []
    for af in raw.get("auto_fail", []):
        auto_fail_rules.append(AutoFailRule(
            name=af["name"],
            description=af.get("description", ""),
            pattern=af.get("pattern"),
            check=af.get("check"),
            expected=af.get("expected"),
            threshold=af.get("threshold"),
        ))

    return Rubric(
        meta=meta,
        dimensions=dimensions,
        pass_threshold=float(raw.get("pass_threshold", 0.70)),
        auto_fail=auto_fail_rules,
    )


def save_rubric(rubric: Rubric, path: str | Path):
    data = {
        "meta": {
            "repo": rubric.meta.repo,
            "feature": rubric.meta.feature,
            "version": rubric.meta.version,
        },
        "dimensions": [],
        "pass_threshold": rubric.pass_threshold,
        "auto_fail": [],
    }
    if rubric.meta.po_spec_doc:
        data["meta"]["po_spec_doc"] = rubric.meta.po_spec_doc
    if rubric.meta.ai_spec_doc:
        data["meta"]["ai_spec_doc"] = rubric.meta.ai_spec_doc
    if rubric.meta.generated_at:
        data["meta"]["generated_at"] = rubric.meta.generated_at
    if rubric.meta.approved_by:
        data["meta"]["approved_by"] = rubric.meta.approved_by
    if rubric.meta.approved_at:
        data["meta"]["approved_at"] = rubric.meta.approved_at

    for dim in rubric.dimensions:
        d = {
            "name": dim.name,
            "weight": dim.weight,
            "description": dim.description,
        }
        if dim.source_behaviors:
            d["source_behaviors"] = dim.source_behaviors
        if dim.scoring:
            d["scoring"] = dim.scoring
        data["dimensions"].append(d)

    for af in rubric.auto_fail:
        rule = {"name": af.name, "description": af.description}
        if af.pattern:
            rule["pattern"] = af.pattern
        if af.check:
            rule["check"] = af.check
        if af.expected:
            rule["expected"] = af.expected
        if af.threshold is not None:
            rule["threshold"] = af.threshold
        data["auto_fail"].append(rule)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False))
