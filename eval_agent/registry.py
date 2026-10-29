from __future__ import annotations

import os
from pathlib import Path

import yaml

from eval_agent.home import get_home
from eval_agent.models import FeatureInfo, RepoInfo, Stage, Status, VersionInfo

REGISTRY_FILE = "registry.yaml"


class Registry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else get_home() / REGISTRY_FILE
        self.repos: dict[str, RepoInfo] = {}
        if self.path.exists():
            self._load()

    def _load(self):
        raw = yaml.safe_load(self.path.read_text()) or {}
        for repo_name, repo_data in raw.get("repos", {}).items():
            features = {}
            for feat_name, feat_data in repo_data.get("features", {}).items():
                versions = {}
                for ver_name, ver_data in feat_data.get("versions", {}).items():
                    versions[ver_name] = VersionInfo(
                        status=Status(ver_data.get("status", "draft")),
                        po_spec_doc=ver_data.get("po_spec_doc"),
                        ai_spec_doc=ver_data.get("ai_spec_doc"),
                        eval_report_doc=ver_data.get("eval_report_doc"),
                        created_at=ver_data.get("created_at", ""),
                        stage=Stage(ver_data["stage"]) if ver_data.get("stage") else None,
                        approved_by=ver_data.get("approved_by", ""),
                        approved_at=ver_data.get("approved_at", ""),
                        approvals=ver_data.get("approvals", []),
                    )
                features[feat_name] = FeatureInfo(
                    adapter=feat_data.get("adapter", "chatbot-sim"),
                    active_version=feat_data.get("active_version"),
                    api_url=feat_data.get("api_url"),
                    versions=versions,
                )
            self.repos[repo_name] = RepoInfo(
                path=repo_data.get("path", ""),
                eval_dir=repo_data.get("eval_dir", "eval"),
                base_url=repo_data.get("base_url", ""),
                features=features,
            )

    def save(self):
        data = {"repos": {}}
        for repo_name, repo in self.repos.items():
            repo_data = {
                "path": repo.path,
                "eval_dir": repo.eval_dir,
                "base_url": repo.base_url,
                "features": {},
            }
            for feat_name, feat in repo.features.items():
                feat_data = {
                    "adapter": feat.adapter,
                    "active_version": feat.active_version,
                }
                if feat.api_url:
                    feat_data["api_url"] = feat.api_url
                versions = {}
                for ver_name, ver in feat.versions.items():
                    ver_data = {"status": ver.status.value}
                    if ver.po_spec_doc:
                        ver_data["po_spec_doc"] = ver.po_spec_doc
                    if ver.ai_spec_doc:
                        ver_data["ai_spec_doc"] = ver.ai_spec_doc
                    if ver.eval_report_doc:
                        ver_data["eval_report_doc"] = ver.eval_report_doc
                    if ver.created_at:
                        ver_data["created_at"] = ver.created_at
                    if ver.stage:
                        ver_data["stage"] = ver.stage.value
                    if ver.approved_by:
                        ver_data["approved_by"] = ver.approved_by
                    if ver.approved_at:
                        ver_data["approved_at"] = ver.approved_at
                    if ver.approvals:
                        ver_data["approvals"] = ver.approvals
                    versions[ver_name] = ver_data
                feat_data["versions"] = versions
                repo_data["features"][feat_name] = feat_data
            data["repos"][repo_name] = repo_data

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True))

    def get_repo(self, name: str) -> RepoInfo:
        if name not in self.repos:
            raise KeyError(f"Repo '{name}' not in registry")
        return self.repos[name]

    def get_feature(self, repo: str, feature: str) -> FeatureInfo:
        r = self.get_repo(repo)
        if feature not in r.features:
            raise KeyError(f"Feature '{feature}' not in repo '{repo}'")
        return r.features[feature]

    def get_active_version(self, repo: str, feature: str) -> VersionInfo:
        feat = self.get_feature(repo, feature)
        if not feat.active_version:
            raise ValueError(f"No active version for {repo}/{feature}")
        return feat.versions[feat.active_version]

    def repo_path(self, repo: str) -> Path:
        """Repo path from the registry; relative paths are relative to the registry file."""
        p = Path(self.get_repo(repo).path).expanduser()
        return p if p.is_absolute() else (self.path.parent / p).resolve()

    def resolve_rubric_path(self, repo: str, feature: str, version: str | None = None) -> Path:
        r = self.get_repo(repo)
        feat = self.get_feature(repo, feature)
        ver = version or feat.active_version
        return self.repo_path(repo) / r.eval_dir / "rubrics" / feature / f"{ver}.yaml"

    def resolve_scope_path(self, repo: str, feature: str, version: str | None = None) -> Path:
        r = self.get_repo(repo)
        feat = self.get_feature(repo, feature)
        ver = version or feat.active_version
        return self.repo_path(repo) / r.eval_dir / "ai-scopes" / feature / f"{ver}.md"

    def resolve_context_path(self, repo: str, feature: str) -> Path:
        """Hand-written notes on how the feature really works; feeds `analyze` so causes are not guessed blind."""
        return self.repo_path(repo) / self.get_repo(repo).eval_dir / "context" / f"{feature}.md"

    def resolve_ai_spec_path(self, repo: str, feature: str, version: str | None = None) -> Path:
        r = self.get_repo(repo)
        feat = self.get_feature(repo, feature)
        ver = version or feat.active_version
        return self.repo_path(repo) / r.eval_dir / "ai-specs" / feature / f"{ver}.md"

    def add_repo(self, name: str, path: str, base_url: str = "", eval_dir: str = "eval"):
        self.repos[name] = RepoInfo(path=path, eval_dir=eval_dir, base_url=base_url)

    def add_feature(self, repo: str, feature: str, adapter: str, api_url: str | None = None):
        r = self.get_repo(repo)
        r.features[feature] = FeatureInfo(adapter=adapter, api_url=api_url)

    def add_version(
        self,
        repo: str,
        feature: str,
        version: str,
        po_spec_doc: str | None = None,
        set_active: bool = True,
    ):
        feat = self.get_feature(repo, feature)
        feat.versions[version] = VersionInfo(
            status=Status.DRAFT,
            po_spec_doc=po_spec_doc,
            created_at=str(datetime.utcnow().date()),
        )
        if set_active:
            if feat.active_version and feat.active_version in feat.versions:
                feat.versions[feat.active_version].status = Status.ARCHIVED
            feat.active_version = version

    def approve_version(self, repo: str, feature: str, version: str, by: str = "", at: str = ""):
        v = self.get_feature(repo, feature).versions[version]
        v.status = Status.APPROVED
        v.stage = Stage.APPROVED
        v.approved_by = by
        v.approved_at = at

    def is_approved(self, repo: str, feature: str, version: str | None = None) -> bool:
        feat = self.get_feature(repo, feature)
        v = feat.versions.get(version or feat.active_version or "")
        return bool(v and v.status == Status.APPROVED)

    def scan_repos(self, base_dir: str) -> list[dict]:
        found = []
        for entry in sorted(Path(base_dir).iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            indicators = []
            for pattern in ["**/system_prompt*", "**/prompts/**", "**/flows/**", "**/graphs/**"]:
                indicators.extend(entry.glob(pattern))
            if indicators:
                registered = entry.name in self.repos or any(
                    r.path == str(entry) for r in self.repos.values()
                )
                found.append({
                    "name": entry.name,
                    "path": str(entry),
                    "registered": registered,
                    "indicators": [str(i.relative_to(entry)) for i in indicators[:5]],
                })
        return found


from datetime import datetime
