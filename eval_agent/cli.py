from __future__ import annotations

import asyncio
import getpass
import hashlib
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from eval_agent import __version__
from eval_agent.home import get_home, init_home, load_env
from eval_agent.models import Stage, Status
from eval_agent.registry import Registry
from eval_agent.store import Store

console = Console()

DEFAULT_MODEL = "Apero-LLM"

HOME = get_home()
load_env(HOME)


def get_registry() -> Registry:
    return Registry(HOME / "registry.yaml")


def get_store() -> Store:
    return Store(HOME / "eval.db")


@click.group()
@click.version_option(__version__, prog_name="eval-agent")
def main():
    """Continuous Eval Agent — evaluate LLM products from PO specs."""
    pass


@main.command("init")
@click.option("--home", "home_dir", type=click.Path(), help="Directory to initialise (default: EVAL_AGENT_HOME or ~/.eval-agent)")
def init_cmd(home_dir: str | None):
    """Create the working directory: registry.yaml, .env, reports/."""
    target = Path(home_dir).expanduser().resolve() if home_dir else Path(os.environ.get("EVAL_AGENT_HOME", "~/.eval-agent")).expanduser().resolve()
    created = init_home(target)
    for path in created:
        console.print(f"  [green]created[/green] {path}")
    if not created:
        console.print(f"[yellow]{target} already initialised — nothing overwritten[/yellow]")
    console.print(f"\nNext: fill in {target / '.env'}, then")
    if target != Path.home() / ".eval-agent":
        console.print(f"  export EVAL_AGENT_HOME={target}")
    console.print("  eval-agent registry add-repo <name> <path>")


# ─── Registry commands ───


@main.group()
def registry():
    """Manage repo/feature registry."""
    pass


@registry.command("list")
def registry_list():
    """List all registered repos and features."""
    reg = get_registry()
    table = Table(title="Registry")
    table.add_column("Repo")
    table.add_column("Feature")
    table.add_column("Adapter")
    table.add_column("Active Ver")
    table.add_column("Status")
    table.add_column("PO Spec Doc")

    for repo_name, repo in reg.repos.items():
        for feat_name, feat in repo.features.items():
            ver = feat.active_version or "-"
            status = "-"
            po_doc = "-"
            if feat.active_version and feat.active_version in feat.versions:
                v = feat.versions[feat.active_version]
                status = v.status.value
                po_doc = v.po_spec_doc or "-"
            table.add_row(repo_name, feat_name, feat.adapter, ver, status, po_doc)

    console.print(table)


@registry.command("scan")
@click.option("--base-dir", default=".", show_default=True, help="Directory to scan for repos")
def registry_scan(base_dir: str):
    """Scan for new repos with AI features."""
    reg = get_registry()
    found = reg.scan_repos(base_dir)
    for item in found:
        icon = "[green]✅[/green]" if item["registered"] else "[yellow]🆕[/yellow]"
        console.print(f"  {icon} {item['name']}")
        if item["indicators"]:
            for ind in item["indicators"][:3]:
                console.print(f"      {ind}", style="dim")


@registry.command("add-repo")
@click.argument("name")
@click.argument("path")
@click.option("--base-url", default="")
def registry_add_repo(name: str, path: str, base_url: str):
    """Add a repo to the registry."""
    reg = get_registry()
    reg.add_repo(name, str(Path(path).expanduser().resolve()), base_url=base_url)
    reg.save()
    console.print(f"[green]Added repo '{name}'[/green]")


@registry.command("add-feature")
@click.argument("repo")
@click.argument("feature")
@click.option("--adapter", default="chatbot-sim")
@click.option("--api-url")
def registry_add_feature(repo: str, feature: str, adapter: str, api_url: str | None):
    """Add a feature to a repo."""
    reg = get_registry()
    reg.add_feature(repo, feature, adapter, api_url)
    reg.save()
    console.print(f"[green]Added feature '{feature}' to '{repo}'[/green]")


# ─── Spec commands ───


@main.group()
def spec():
    """PO Spec → AI Spec pipeline."""
    pass


@spec.command("parse")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--po-doc", help="AFFiNE docId for PO spec")
@click.option("--po-file", type=click.Path(exists=True), help="Local file path for PO spec")
@click.option("--model", envvar="LLM_MODEL_NAME", default=DEFAULT_MODEL, show_default=True)
@click.option("--base-url", envvar="LLM_BASE_URL")
@click.option("--api-key", envvar="LLM_API_KEY", default="not-needed")
def spec_parse(repo: str, feature: str, po_doc: str | None, po_file: str | None, model: str, base_url: str, api_key: str):
    """Parse PO spec and extract AI scope."""
    from eval_agent.spec_parser import parse_po_spec

    if po_file:
        spec_text = Path(po_file).read_text()
    elif po_doc:
        console.print(f"[dim]Reading PO spec from AFFiNE doc {po_doc}...[/dim]")
        console.print("[yellow]AFFiNE integration requires MCP — use --po-file for local testing[/yellow]")
        return
    else:
        console.print("[red]Provide --po-doc or --po-file[/red]")
        return

    async def _run():
        scope = await parse_po_spec(spec_text, model=model, base_url=base_url, api_key=api_key)
        console.print(f"\n[green]✅ AI behaviors:[/green] {len(scope.ai_items)} items")
        console.print(f"[red]❌ Filtered out:[/red] {len(scope.non_ai_items)} items")
        for item in scope.ai_items:
            console.print(f"  [green]•[/green] {item}")
        return scope

    asyncio.run(_run())


@spec.command("generate")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--scope-file", type=click.Path(exists=True), help="AI scope markdown file")
@click.option("--model", envvar="LLM_MODEL_NAME", default=DEFAULT_MODEL, show_default=True)
@click.option("--base-url", envvar="LLM_BASE_URL")
@click.option("--api-key", envvar="LLM_API_KEY", default="not-needed")
def spec_generate(repo: str, feature: str, scope_file: str | None, model: str, base_url: str, api_key: str):
    """Generate AI Spec from AI scope."""
    from eval_agent.spec_parser import AIScope, generate_ai_spec

    if not scope_file:
        console.print("[red]Provide --scope-file[/red]")
        return

    scope = AIScope(raw=Path(scope_file).read_text())

    async def _run():
        ai_spec = await generate_ai_spec(scope, repo, feature, model=model, base_url=base_url, api_key=api_key)
        console.print(f"\n[green]✅ AI Spec generated with {len(ai_spec.behaviors)} behaviors[/green]")
        for b in ai_spec.behaviors:
            console.print(f"  {b}")

        reg = get_registry()
        out_path = reg.resolve_ai_spec_path(repo, feature)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(ai_spec.raw)
        console.print(f"\n[dim]Saved → {out_path}[/dim]")
        return ai_spec

    asyncio.run(_run())


@spec.command("pipeline")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--version", required=True)
@click.option("--po-file", type=click.Path(exists=True), required=True)
@click.option("--model", envvar="LLM_MODEL_NAME", default=DEFAULT_MODEL, show_default=True)
@click.option("--base-url", envvar="LLM_BASE_URL")
@click.option("--api-key", envvar="LLM_API_KEY", default="not-needed")
def spec_pipeline(repo: str, feature: str, version: str, po_file: str, model: str, base_url: str, api_key: str):
    """Start the PO spec → rubric workflow; stops at CP1 for human review of the AI scope."""
    from eval_agent.spec_parser import parse_po_spec

    reg = get_registry()
    existing = reg.get_feature(repo, feature).versions.get(version)
    if existing and existing.status == Status.APPROVED:
        console.print(f"[red]{repo}/{feature} {version} is already approved — start a new version instead[/red]")
        return

    spec_text = Path(po_file).read_text()
    console.print("Extracting AI scope from the PO spec...")
    scope = asyncio.run(parse_po_spec(spec_text, model=model, base_url=base_url, api_key=api_key))
    console.print(f"  {len(scope.ai_items)} AI items kept, {len(scope.non_ai_items)} filtered out")

    if not existing:
        reg.add_version(repo, feature, version)
    scope_path = reg.resolve_scope_path(repo, feature, version)
    scope_path.parent.mkdir(parents=True, exist_ok=True)
    scope_path.write_text(scope.raw)
    reg.get_feature(repo, feature).versions[version].stage = Stage.SCOPE_REVIEW
    reg.save()

    print_checkpoint(1, "Review the AI scope", scope_path, repo, feature, version,
                     "Check nothing AI-related was filtered out and nothing non-AI slipped in. Edit the file directly.")


# ─── Checkpoints ───

CHECKPOINTS = {
    Stage.SCOPE_REVIEW: (1, "Review the AI scope"),
    Stage.SPEC_REVIEW: (2, "Review the AI spec"),
    Stage.RUBRIC_REVIEW: (3, "Review the rubric"),
}


def print_checkpoint(n: int, title: str, path: Path, repo: str, feature: str, version: str, hint: str):
    console.print(f"\n[bold yellow]⏸  CP{n} — {title} (waiting for you)[/bold yellow]")
    console.print(f"  File: {path}")
    console.print(f"  {hint}")
    console.print(f"  When it is right: eval-agent approve --repo {repo} --feature {feature} --version {version}")


def reviewer_name(by: str | None) -> str:
    if by:
        return by
    if os.environ.get("EVAL_AGENT_USER"):
        return os.environ["EVAL_AGENT_USER"]
    git = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True)
    return git.stdout.strip() or getpass.getuser()


def stamp_rubric(path: Path, by: str, at: str):
    """Write approved_by / approved_at into the rubric's meta block, keeping the rest of the file as is."""
    lines = [l for l in path.read_text().splitlines() if not l.strip().startswith(("approved_by:", "approved_at:"))]
    idx = next((i for i, l in enumerate(lines) if l.strip() == "meta:"), None)
    stamp = [f'  approved_by: "{by}"', f'  approved_at: "{at}"']
    lines = lines[: idx + 1] + stamp + lines[idx + 1:] if idx is not None else ["meta:", *stamp, *lines]
    path.write_text("\n".join(lines) + "\n")


def approve_rubric(reg: Registry, repo: str, feature: str, version: str, by: str) -> bool:
    rubric_path = reg.resolve_rubric_path(repo, feature, version)
    draft_path = rubric_path.with_suffix(".draft.yaml")
    if draft_path.exists():
        draft_path.replace(rubric_path)
    elif not rubric_path.exists():
        console.print(f"[red]No rubric or draft at {rubric_path.parent}[/red]")
        return False
    at = datetime.now().isoformat(timespec="seconds")
    stamp_rubric(rubric_path, by, at)
    reg.approve_version(repo, feature, version, by=by, at=at)
    reg.save()
    console.print(f"[green]✅ CP3 passed — rubric {rubric_path.name} approved by {by}[/green]")
    console.print(f"  Next: eval-agent run --repo {repo} --feature {feature}   (or `eval` with a transcript)")
    return True


@main.command("approve")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--version", help="Defaults to the active version")
@click.option("--by", help="Reviewer name (default: EVAL_AGENT_USER, git user.name, or OS user)")
@click.option("--model", envvar="LLM_MODEL_NAME", default=DEFAULT_MODEL, show_default=True)
@click.option("--base-url", envvar="LLM_BASE_URL")
@click.option("--api-key", envvar="LLM_API_KEY", default="not-needed")
def approve_cmd(repo: str, feature: str, version: str | None, by: str | None, model: str, base_url: str, api_key: str):
    """Pass the pending checkpoint (CP1 → CP2 → CP3) and run the next step."""
    from eval_agent.spec_parser import AIScope, AISpec, generate_ai_spec, generate_rubric_yaml

    reg = get_registry()
    feat = reg.get_feature(repo, feature)
    version = version or feat.active_version
    v = feat.versions.get(version or "")
    if not v:
        console.print(f"[red]{repo}/{feature} has no version {version}[/red]")
        return
    by = reviewer_name(by)
    stage = v.stage
    if stage is None and v.status != Status.APPROVED:
        # Versions created before checkpoints existed: only the rubric is left to approve
        stage = Stage.RUBRIC_REVIEW

    if stage == Stage.SCOPE_REVIEW:
        scope_path = reg.resolve_scope_path(repo, feature, version)
        v.approvals.append(f"CP1 {by} {datetime.now().isoformat(timespec='seconds')}")
        console.print(f"[green]✅ CP1 passed — AI scope approved by {by}[/green]\nWriting the AI spec...")
        ai_spec = asyncio.run(generate_ai_spec(AIScope(raw=scope_path.read_text()), repo, feature,
                                               model=model, base_url=base_url, api_key=api_key))
        spec_path = reg.resolve_ai_spec_path(repo, feature, version)
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(ai_spec.raw)
        v.stage = Stage.SPEC_REVIEW
        reg.save()
        print_checkpoint(2, "Review the AI spec", spec_path, repo, feature, version,
                         "Each behavior must be testable and match what the PO wants. Edit the file directly.")
    elif stage == Stage.SPEC_REVIEW:
        spec_path = reg.resolve_ai_spec_path(repo, feature, version)
        v.approvals.append(f"CP2 {by} {datetime.now().isoformat(timespec='seconds')}")
        console.print(f"[green]✅ CP2 passed — AI spec approved by {by}[/green]\nDrafting the rubric...")
        rubric_yaml = asyncio.run(generate_rubric_yaml(AISpec(raw=spec_path.read_text()), repo, feature, version,
                                                       model=model, base_url=base_url, api_key=api_key))
        draft_path = reg.resolve_rubric_path(repo, feature, version).with_suffix(".draft.yaml")
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(rubric_yaml)
        v.stage = Stage.RUBRIC_REVIEW
        reg.save()
        print_checkpoint(3, "Review the rubric", draft_path, repo, feature, version,
                         "Check dimensions, weights, pass_threshold and auto_fail. Leave out what code already checks.")
    elif stage == Stage.RUBRIC_REVIEW:
        approve_rubric(reg, repo, feature, version, by)
    else:
        console.print(f"[yellow]{repo}/{feature} {version} is already approved — nothing pending[/yellow]")


# ─── Rubric commands ───


@main.group()
def rubric():
    """Manage evaluation rubrics."""
    pass


@rubric.command("list")
def rubric_list():
    """List all rubrics across repos."""
    reg = get_registry()
    table = Table(title="Rubrics")
    table.add_column("Repo")
    table.add_column("Feature")
    table.add_column("Version")
    table.add_column("Dims")
    table.add_column("Status")

    for repo_name, repo in reg.repos.items():
        for feat_name, feat in repo.features.items():
            ver = feat.active_version or "-"
            rubric_path = reg.resolve_rubric_path(repo_name, feat_name)
            dims = "-"
            if rubric_path.exists():
                from eval_agent.rubric_loader import load_rubric
                r = load_rubric(rubric_path)
                dims = str(len(r.dimensions))
            status = "-"
            if feat.active_version and feat.active_version in feat.versions:
                status = feat.versions[feat.active_version].status.value
            table.add_row(repo_name, feat_name, ver, dims, status)

    console.print(table)


@rubric.command("approve")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--version", required=True)
@click.option("--by", help="Reviewer name (default: EVAL_AGENT_USER, git user.name, or OS user)")
def rubric_approve(repo: str, feature: str, version: str, by: str | None):
    """Approve a draft rubric (CP3). Same as `approve` when CP3 is the pending checkpoint."""
    reg = get_registry()
    v = reg.get_feature(repo, feature).versions.get(version)
    if not v:
        console.print(f"[red]{repo}/{feature} has no version {version} — run `spec pipeline` first[/red]")
        return
    if v.stage in (Stage.SCOPE_REVIEW, Stage.SPEC_REVIEW):
        n, title = CHECKPOINTS[v.stage]
        console.print(f"[red]CP{n} ({title}) is still pending — run `eval-agent approve` first[/red]")
        return
    if v.status == Status.APPROVED:
        console.print(f"[yellow]Rubric for {version} already approved by {v.approved_by or '?'} at {v.approved_at or '?'}[/yellow]")
        return
    approve_rubric(reg, repo, feature, version, reviewer_name(by))


@rubric.command("versions")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
def rubric_versions(repo: str, feature: str):
    """List every rubric version of a feature: status, who approved it, content hash."""
    from eval_agent.judge import file_hash

    reg = get_registry()
    feat = reg.get_feature(repo, feature)
    table = Table(title=f"Rubric versions — {repo}/{feature}")
    for col in ("Version", "Active", "Status", "Stage", "Approved by", "Approved at", "Created", "Hash"):
        table.add_column(col)
    for name, v in feat.versions.items():
        path = reg.resolve_rubric_path(repo, feature, name)
        draft = path.with_suffix(".draft.yaml")
        h = file_hash(path) if path.exists() else (file_hash(draft) + " (draft)" if draft.exists() else "-")
        table.add_row(name, "●" if name == feat.active_version else "", v.status.value, v.stage.value if v.stage else "-",
                      v.approved_by or "-", v.approved_at or "-", v.created_at or "-", h)
    console.print(table)


@rubric.command("new-version")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--to", "to_version", required=True, help="New version name, e.g. v1.1.1")
@click.option("--from", "from_version", help="Version to start from (default: active)")
def rubric_new_version(repo: str, feature: str, to_version: str, from_version: str | None):
    """Start a new rubric version as a draft copy; the current version stays active until CP3 approves the new one."""
    reg = get_registry()
    feat = reg.get_feature(repo, feature)
    from_version = from_version or feat.active_version
    if to_version in feat.versions:
        console.print(f"[red]{to_version} already exists[/red]")
        return
    src = reg.resolve_rubric_path(repo, feature, from_version)
    if not src.exists():
        console.print(f"[red]No approved rubric for {from_version} at {src}[/red]")
        return
    lines = [l for l in src.read_text().splitlines() if not l.strip().startswith(("approved_by:", "approved_at:"))]
    lines = [f'  version: "{to_version}"' if l.strip().startswith("version:") and l.startswith("  ") else l for l in lines]
    draft = reg.resolve_rubric_path(repo, feature, to_version).with_suffix(".draft.yaml")
    draft.write_text("\n".join(lines) + "\n")
    spec_src = reg.resolve_ai_spec_path(repo, feature, from_version)
    if spec_src.exists():
        spec_dst = reg.resolve_ai_spec_path(repo, feature, to_version)
        spec_dst.write_text(spec_src.read_text())
    reg.add_version(repo, feature, to_version, stage=Stage.RUBRIC_REVIEW)
    reg.save()
    console.print(f"[green]{to_version} created from {from_version}; {from_version} stays active until {to_version} is approved[/green]")
    print_checkpoint(3, "Review the rubric", draft, repo, feature, to_version,
                     f"Edit the draft, then compare: eval-agent rubric diff --repo {repo} --feature {feature} --from {from_version} --to {to_version}")


@rubric.command("diff")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--from", "from_version", required=True)
@click.option("--to", "to_version", required=True)
def rubric_diff(repo: str, feature: str, from_version: str, to_version: str):
    """Unified diff between two rubric versions (uses the draft when a version is not approved yet)."""
    import difflib

    reg = get_registry()

    def path_of(ver: str) -> Path | None:
        p = reg.resolve_rubric_path(repo, feature, ver)
        return p if p.exists() else (p.with_suffix(".draft.yaml") if p.with_suffix(".draft.yaml").exists() else None)

    a, b = path_of(from_version), path_of(to_version)
    if not a or not b:
        console.print(f"[red]Missing rubric for {from_version if not a else to_version}[/red]")
        return
    diff = difflib.unified_diff(a.read_text().splitlines(), b.read_text().splitlines(),
                                fromfile=f"{from_version} ({a.name})", tofile=f"{to_version} ({b.name})", lineterm="")
    out = "\n".join(diff)
    print(out or "No differences")


@rubric.command("show")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--version")
def rubric_show(repo: str, feature: str, version: str | None):
    """Show rubric details."""
    reg = get_registry()
    rubric_path = reg.resolve_rubric_path(repo, feature, version)
    if not rubric_path.exists():
        console.print(f"[red]Rubric not found: {rubric_path}[/red]")
        return

    from eval_agent.rubric_loader import load_rubric
    r = load_rubric(rubric_path)

    console.print(f"[bold]{r.meta.repo}/{r.meta.feature} v{r.meta.version}[/bold]")
    console.print(f"Pass threshold: {r.pass_threshold}")
    console.print()

    table = Table()
    table.add_column("Dimension")
    table.add_column("Weight", justify="right")
    table.add_column("Behaviors")
    table.add_column("Description")

    for dim in r.dimensions:
        table.add_row(
            dim.name,
            f"{dim.weight:.1f}",
            ", ".join(dim.source_behaviors) if dim.source_behaviors else "-",
            dim.description[:80] + "..." if len(dim.description) > 80 else dim.description,
        )
    console.print(table)

    if r.auto_fail:
        console.print("\n[bold]Auto-fail rules:[/bold]")
        for af in r.auto_fail:
            console.print(f"  [red]✕[/red] {af.name}: {af.description}")


# ─── Eval commands ───


@main.command("eval")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--transcript", type=click.Path(exists=True), required=True, help="Path to transcript JSON")
@click.option("--config-file", type=click.Path(exists=True), help="Path to flow config JSON")
@click.option("--user-level")
@click.option("--model", envvar=["JUDGE_MODEL", "LLM_MODEL_NAME"], default=DEFAULT_MODEL, show_default=True)
@click.option("--base-url", envvar=["JUDGE_BASE_URL", "LLM_BASE_URL"])
@click.option("--api-key", envvar=["JUDGE_API_KEY", "LLM_API_KEY"], default="not-needed")
@click.option("--save/--no-save", default=True, help="Save result to store")
@click.option("--runs", default=3, show_default=True, help="Judge runs; per-dimension median is used")
@click.option("--allow-draft", is_flag=True, help="Try an unapproved rubric; results are not saved")
def eval_cmd(repo: str, feature: str, transcript: str, config_file: str | None, user_level: str | None, model: str, base_url: str, api_key: str, save: bool, runs: int, allow_draft: bool):
    """Judge a transcript against a rubric."""
    from eval_agent.judge import file_hash, judge_prompt_hash, judge_transcript

    reg = get_registry()
    rubric_path, approved = gated_rubric(reg, repo, feature, allow_draft)
    if not rubric_path:
        return
    save = save and approved
    batch_id = new_batch_id()

    transcript_data = json.loads(Path(transcript).read_text())
    if isinstance(transcript_data, dict) and "messages" in transcript_data:
        messages = transcript_data["messages"]
    elif isinstance(transcript_data, dict) and "turns" in transcript_data:
        messages = transcript_data["turns"]
    elif isinstance(transcript_data, list):
        messages = transcript_data
    else:
        console.print("[red]Cannot parse transcript — expected list or dict with 'messages'/'turns'[/red]")
        return

    config = json.loads(Path(config_file).read_text()) if config_file else None

    async def _run():
        result = await judge_transcript(
            transcript=messages,
            rubric_path=rubric_path,
            config=config,
            user_level=user_level,
            model=model,
            base_url=base_url,
            api_key=api_key,
            runs=runs,
        )

        verdict_color = "green" if result.verdict == "PASS" else "red"
        console.print(f"\n[bold {verdict_color}]{result.verdict.value}[/bold {verdict_color}] — overall: {result.overall:.2f} (threshold: {result.pass_threshold})")

        if result.auto_fail:
            console.print("[red bold]AUTO-FAIL triggered:[/red bold]")
            for af in result.auto_fail_reasons:
                console.print(f"  [red]✕[/red] {af.name}: {af.matched}")
        else:
            table = Table()
            table.add_column("Dimension")
            table.add_column("Score", justify="right")
            table.add_column("Runs", justify="right")
            table.add_column("Weight", justify="right")
            table.add_column("Weighted", justify="right")
            table.add_column("Rationale")

            for dim in result.dimensions:
                score_style = "red" if dim.score < 5 else "yellow" if dim.score < 7 else "green"
                table.add_row(
                    dim.name,
                    f"[{score_style}]{dim.score:.1f}[/{score_style}]",
                    " ".join(f"{x:.0f}" for x in dim.run_scores),
                    f"{dim.weight:.1f}",
                    f"{dim.weighted_score:.1f}",
                    dim.rationale[:60] + "..." if len(dim.rationale) > 60 else dim.rationale,
                )
            console.print(table)

        if save:
            from eval_agent.models import EvalResult, Verdict as V
            feat = reg.get_feature(repo, feature)
            config_hash = hashlib.sha256(json.dumps(config or {}, sort_keys=True).encode()).hexdigest()[:16]
            eval_result = EvalResult(
                repo=repo,
                feature=feature,
                rubric_ver=feat.active_version or "unknown",
                config_hash=config_hash,
                config_name=Path(config_file).stem if config_file else "",
                model=model,
                trigger="manual",
                verdict=V(result.verdict.value),
                overall=result.overall,
                dimensions=result.dimensions,
                auto_fail=result.auto_fail,
                total_turns=len(messages),
                judge_model=model,
                user_level=user_level or "",
                transcript=messages,
                judge_prompt_hash=judge_prompt_hash(),
                rubric_hash=file_hash(rubric_path),
                tool_version=__version__,
            )
            store = get_store()
            run_id = store.save_eval_run(eval_result, batch_id)
            console.print(f"\n[dim]Saved run: {run_id} (batch {batch_id})[/dim]")

            regression = store.detect_regression(eval_result)
            if regression:
                sev = regression["severity"]
                sev_color = "red" if sev == "critical" else "yellow"
                console.print(f"\n[{sev_color} bold]⚠️ REGRESSION ({sev}):[/{sev_color} bold] delta={regression['overall_delta']:.3f}")
                for w in regression["worst_dimensions"]:
                    console.print(f"  {w['dimension']}: {w['score']:.1f} (was {w['baseline']:.1f}, delta={w['delta']:.1f})")
            store.close()

    asyncio.run(_run())


# ─── Run (adapter) commands ───


def new_batch_id() -> str:
    return f"b-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(2).hex()}"


def gated_rubric(reg: Registry, repo: str, feature: str, allow_draft: bool) -> tuple[Path | None, bool]:
    """Rubric to judge with, and whether it is approved. Unapproved rubrics only run with --allow-draft."""
    feat = reg.get_feature(repo, feature)
    version = feat.active_version
    rubric_path = reg.resolve_rubric_path(repo, feature)
    if reg.is_approved(repo, feature):
        if not rubric_path.exists():
            console.print(f"[red]Rubric not found: {rubric_path}[/red]")
            return None, False
        return rubric_path, True

    v = feat.versions.get(version or "")
    pending = CHECKPOINTS.get(v.stage) if v and v.stage else (3, "Review the rubric")
    if not allow_draft:
        console.print(f"[red]⛔ {repo}/{feature} {version}: rubric not approved — CP{pending[0]} ({pending[1]}) is pending[/red]")
        console.print(f"  Approve: eval-agent approve --repo {repo} --feature {feature}")
        console.print("  Or try it without saving results: add --allow-draft")
        return None, False
    draft_path = rubric_path.with_suffix(".draft.yaml")
    path = draft_path if draft_path.exists() else rubric_path
    if not path.exists():
        console.print(f"[red]No rubric or draft at {rubric_path.parent}[/red]")
        return None, False
    console.print(f"[yellow]⚠ Using unapproved rubric {path.name} — results will NOT be saved[/yellow]")
    return path, False


@main.command("run")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--only", multiple=True, help="Only these profile/request ids (repeatable)")
@click.option("--model", envvar=["JUDGE_MODEL", "LLM_MODEL_NAME"], default=DEFAULT_MODEL, show_default=True)
@click.option("--base-url", envvar=["JUDGE_BASE_URL", "LLM_BASE_URL"])
@click.option("--api-key", envvar=["JUDGE_API_KEY", "LLM_API_KEY"], default="not-needed")
@click.option("--runs", default=3, show_default=True, help="Judge runs per item; per-dimension median is used")
@click.option("--concurrency", default=4, show_default=True, help="Items judged in parallel")
@click.option("--save/--no-save", default=True, help="Save results to store")
@click.option("--set-baseline", is_flag=True, help="Make each saved run the baseline for its profile")
@click.option("--allow-draft", is_flag=True, help="Try an unapproved rubric; results are not saved")
def run_cmd(repo: str, feature: str, only: tuple[str, ...], model: str, base_url: str, api_key: str,
            runs: int, concurrency: int, save: bool, set_baseline: bool, allow_draft: bool):
    """Generate outputs with the feature's adapter, then judge each one."""
    from eval_agent.judge import file_hash, judge_prompt_hash, judge_transcript
    from eval_agent.models import EvalResult, Verdict as V

    reg = get_registry()
    feat = reg.get_feature(repo, feature)
    rubric_path, approved = gated_rubric(reg, repo, feature, allow_draft)
    if not rubric_path:
        return
    save = save and approved
    set_baseline = set_baseline and save
    batch_id = new_batch_id()
    if feat.adapter != "refyn-plan":
        console.print(f"[red]Adapter '{feat.adapter}' has no run support yet — use `eval` with a transcript[/red]")
        return

    from eval_agent.adapters import refyn_plan as adapter

    console.print(f"[dim]Generating outputs in {reg.repo_path(repo)}...[/dim]")
    items = adapter.generate_plans(reg.repo_path(repo), list(only))
    if not items:
        console.print(f"[red]No items to evaluate{' for --only ' + ', '.join(only) if only else ''}[/red]")
        return
    console.print(f"[dim]{len(items)} items, judging with {model} ({runs} runs each)...[/dim]")

    async def _run():
        sem = asyncio.Semaphore(concurrency)

        async def judge_item(item: dict):
            if item["output"] is None:
                return item, None, None
            transcript = adapter.to_transcript(item["input"], item["output"])
            async with sem:
                try:
                    result = await judge_transcript(
                        transcript=transcript,
                        rubric_path=rubric_path,
                        model=model,
                        base_url=base_url,
                        api_key=api_key,
                        runs=runs,
                    )
                except Exception as e:
                    # One bad judge response must not lose the rest of the batch
                    item["error"] = f"judge failed: {type(e).__name__}: {e}"
                    console.print(f"  [red]judge failed {item['input']['request_id']}: {e}[/red]")
                    return item, None, None
            console.print(f"  [dim]judged {item['input']['request_id']}: {result.verdict.value} {result.overall:.2f}[/dim]")
            return item, transcript, result

        return await asyncio.gather(*[judge_item(i) for i in items])

    results = asyncio.run(_run())

    store = get_store() if save else None
    dim_names = [d.name for r in results if r[2] and r[2].dimensions for d in r[2].dimensions]
    dim_names = list(dict.fromkeys(dim_names))
    table = Table(title=f"{repo} / {feature} {feat.active_version}")
    table.add_column("Id")
    table.add_column("Profile")
    table.add_column("Verdict")
    table.add_column("Overall", justify="right")
    for name in dim_names:
        table.add_column(name.split("_")[0][:8], justify="right")
    table.add_column("Regr")

    failures = []
    for item, transcript, result in results:
        profile = item["input"]
        rid = profile["request_id"]
        if result is None:
            table.add_row(rid, adapter.profile_label(profile), "[red]ERROR[/red]", "-", *["-"] * len(dim_names), "")
            failures.append((rid, item.get("error", ""), None))
            continue

        regr = ""
        if store:
            config_hash = hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()[:16]
            eval_result = EvalResult(
                repo=repo,
                feature=feature,
                rubric_ver=feat.active_version or "unknown",
                config_hash=config_hash,
                config_name=rid,
                model=item["output"].get("versions", {}).get("selection", ""),
                trigger="run",
                verdict=V(result.verdict.value),
                overall=result.overall,
                dimensions=result.dimensions,
                auto_fail=result.auto_fail,
                total_turns=len(transcript),
                judge_model=model,
                persona=adapter.profile_label(profile),
                transcript=transcript,
                judge_prompt_hash=judge_prompt_hash(),
                rubric_hash=file_hash(rubric_path),
                tool_version=__version__,
            )
            run_id = store.save_eval_run(eval_result, batch_id)
            regression = store.detect_regression(eval_result)
            if regression:
                color = "red" if regression["severity"] == "critical" else "yellow"
                regr = f"[{color}]{regression['overall_delta']:+.2f}[/{color}]"
            if set_baseline:
                store.set_baseline(run_id)

        scores = {d.name: d.score for d in result.dimensions}
        color = "green" if result.verdict == V.PASS else "red"
        table.add_row(
            rid,
            adapter.profile_label(profile),
            f"[{color}]{result.verdict.value}[/{color}]",
            f"{result.overall:.2f}",
            *[f"{scores[n]:.0f}" if n in scores else "-" for n in dim_names],
            regr,
        )
        if result.verdict != V.PASS:
            failures.append((rid, None, result))

    console.print(table)

    judged = [r for _, _, r in results if r is not None]
    if judged:
        passed = sum(r.verdict == V.PASS for r in judged)
        console.print(f"\n[bold]{passed}/{len(judged)} PASS[/bold] · mean overall {sum(r.overall for r in judged) / len(judged):.2f}")
        for name in dim_names:
            vals = [d.score for r in judged for d in r.dimensions if d.name == name]
            console.print(f"  {name:32s} avg {sum(vals) / len(vals):.1f}")

    for rid, error, result in failures:
        console.print(f"\n[bold red]{rid}[/bold red]")
        if error:
            console.print(f"  {error}")
            continue
        for af in result.auto_fail_reasons:
            console.print(f"  [red]AUTO-FAIL {af.name}:[/red] {af.matched}")
        for dim in sorted(result.dimensions, key=lambda d: d.score)[:2]:
            console.print(f"  {dim.name} = {dim.score:.0f}: {dim.rationale}")
            for miss in dim.misses[:3]:
                console.print(f"    - {miss.get('quote', '')} → {miss.get('issue', '')}", style="dim")

    if store:
        console.print(f"\nBatch: {batch_id}")
        if set_baseline:
            console.print("[green]Baselines set for every saved run[/green]")
        store.close()
        console.print("\n[bold yellow]⏸  Your call:[/bold yellow]")
        console.print(f"  • CP4 — draft fix suggestions to review: eval-agent analyze --repo {repo} --feature {feature}")
        if not set_baseline:
            console.print(f"  • Accept this batch as the new reference: eval-agent baseline set --batch {batch_id}")


# ─── History commands ───


@main.command("batches")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--limit", default=20, show_default=True)
def batches_cmd(repo: str, feature: str, limit: int):
    """List eval batches with the versions they were judged with."""
    store = get_store()
    rows = store.batches(repo, feature, limit)
    store.close()
    table = Table(title=f"Batches — {repo}/{feature}")
    for col in ("Batch", "Date", "Items", "PASS", "Mean", "Rubric", "Rubric hash", "Judge prompt", "Judge model", "Tool"):
        table.add_column(col)
    for b in rows:
        table.add_row(b["batch_id"], b["created_at"][:16], str(b["n"]), str(b["passed"]), f"{b['mean']:.2f}",
                      b["rubric_ver"] or "-", b["rubric_hash"] or "-", b["judge_prompt_hash"] or "-",
                      b["judge_model"] or "-", b["tool_version"] or "-")
    console.print(table)


@main.command("compare")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--a", "batch_a", help="Older batch (default: the one before --b)")
@click.option("--b", "batch_b", help="Newer batch (default: latest)")
def compare_cmd(repo: str, feature: str, batch_a: str | None, batch_b: str | None):
    """Compare two batches item by item; warns when they were judged with different versions."""
    store = get_store()
    all_batches = store.batches(repo, feature, limit=200)
    ids = [b["batch_id"] for b in all_batches]
    batch_b = batch_b or (ids[0] if ids else None)
    if not batch_a and batch_b in ids and ids.index(batch_b) + 1 < len(ids):
        batch_a = ids[ids.index(batch_b) + 1]
    if not batch_a or not batch_b:
        console.print("[red]Need two batches to compare[/red]")
        store.close()
        return
    meta = {b["batch_id"]: b for b in all_batches}
    runs_a, runs_b = store.batch_runs(batch_a), store.batch_runs(batch_b)
    store.close()

    console.print(f"[bold]{batch_a}[/bold]  →  [bold]{batch_b}[/bold]")
    differs = []
    for key, label in (("rubric_ver", "rubric version"), ("rubric_hash", "rubric content"),
                       ("judge_prompt_hash", "judge prompt"), ("judge_model", "judge model"), ("tool_version", "eval-agent")):
        va, vb = meta.get(batch_a, {}).get(key), meta.get(batch_b, {}).get(key)
        mark = "" if va == vb else "  [yellow]⚠ changed[/yellow]"
        console.print(f"  {label:15s} {va or '-'} → {vb or '-'}{mark}")
        if va != vb and key != "tool_version":
            differs.append(label)
    if differs:
        console.print(f"[yellow]⚠ Not a like-for-like comparison: {', '.join(differs)} changed. "
                      "Score changes may come from the judge or rubric, not from the product.[/yellow]")

    # Same item = same name, or for unnamed `eval` runs the same input config
    by_a = {r["config_name"] or r["config_hash"]: r for r in runs_a}
    by_b = {r["config_name"] or r["config_hash"]: r for r in runs_b}
    common = [k for k in by_b if k in by_a]
    table = Table(title=f"{len(common)} items in both batches")
    for col in ("Item", "Overall A", "Overall B", "Δ", "Verdict"):
        table.add_column(col, justify="right" if col.startswith(("Overall", "Δ")) else "left")
    for k in common:
        a, b = by_a[k], by_b[k]
        d = b["overall"] - a["overall"]
        color = "green" if d > 0.05 else "red" if d < -0.05 else "white"
        table.add_row(k, f"{a['overall']:.2f}", f"{b['overall']:.2f}", f"[{color}]{d:+.2f}[/{color}]",
                      f"{a['verdict']} → {b['verdict']}")
    console.print(table)

    dims: dict[str, list[float]] = {}
    for k in common:
        sa = {d["dimension"]: d["score"] for d in by_a[k]["dimensions"]}
        for d in by_b[k]["dimensions"]:
            if d["dimension"] in sa:
                dims.setdefault(d["dimension"], []).append(d["score"] - sa[d["dimension"]])
    for name, deltas in dims.items():
        console.print(f"  {name:32s} mean Δ {sum(deltas) / len(deltas):+.1f}")
    only_a = sorted(set(by_a) - set(by_b))
    only_b = sorted(set(by_b) - set(by_a))
    if only_a or only_b:
        console.print(f"[dim]Only in A: {', '.join(only_a) or '-'} · only in B: {', '.join(only_b) or '-'}[/dim]")


@main.command("history")
@click.argument("repo")
@click.option("--feature")
@click.option("--version")
@click.option("--limit", default=20)
def history_cmd(repo: str, feature: str | None, version: str | None, limit: int):
    """View eval history for a repo."""
    store = get_store()
    runs = store.list_runs(repo=repo, feature=feature, rubric_ver=version, limit=limit)

    table = Table(title=f"Eval History — {repo}")
    table.add_column("Date")
    table.add_column("Feature")
    table.add_column("Version")
    table.add_column("Overall", justify="right")
    table.add_column("Verdict")
    table.add_column("Trigger")

    for run in runs:
        verdict_style = "green" if run["verdict"] == "PASS" else "red"
        table.add_row(
            run["created_at"][:16],
            run["feature"],
            run["rubric_ver"],
            f"{run['overall']:.2f}",
            f"[{verdict_style}]{run['verdict']}[/{verdict_style}]",
            run["trigger"],
        )
    console.print(table)
    store.close()


@main.command("baseline")
@click.argument("action", type=click.Choice(["set", "show"]))
@click.option("--run-id", help="Run ID to set as baseline")
@click.option("--batch", help="Batch ID: set every run in it as the baseline for its config/profile")
@click.option("--repo")
@click.option("--feature")
def baseline_cmd(action: str, run_id: str | None, batch: str | None, repo: str | None, feature: str | None):
    """Manage baselines."""
    store = get_store()
    if action == "set":
        if not run_id and not batch:
            console.print("[red]--run-id or --batch required for set[/red]")
            return
        run_ids = [run_id] if run_id else [r["id"] for r in store.batch_runs(batch)]
        if not run_ids:
            console.print(f"[red]No runs in batch {batch}[/red]")
            return
        for rid in run_ids:
            store.set_baseline(rid)
        console.print(f"[green]✅ Baseline set for {len(run_ids)} run(s)[/green]")
    elif action == "show":
        if not repo or not feature:
            console.print("[red]--repo and --feature required for show[/red]")
            return
        reg = get_registry()
        feat = reg.get_feature(repo, feature)
        baselines = store.list_active_baselines(repo, feature, feat.active_version or "")
        for b in baselines:
            console.print(f"Baseline {b['config_name'] or b['config_hash']}: overall={b['overall']:.2f}, run={b['run_id']}")
        if not baselines:
            console.print("[yellow]No active baseline[/yellow]")
    store.close()


# ─── CP4: analyze → report review → send ───

ANALYZE_PROMPT = """You review the results of an automated evaluation of an AI feature and turn them
into a short list of issues for the engineers who own it.

Repo: {repo} · Feature: {feature} · Rubric: {rubric_ver}
{context}
Below are the low-scoring dimensions per evaluated item, with the judge's rationale
and the verbatim evidence it cited.

{evidence}

Write markdown with exactly one section, "## Issues", listing at most 8 distinct issues,
most important first. Merge the same problem seen in several items into one issue.
For each issue:
### <n>. <short title>
- **Severity:** high / medium / low
- **Affected:** item ids
- **Evidence:** 2-4 short quotes copied exactly from the evidence above (with item id)
- **Likely cause (hypothesis, unverified):** where in the system this probably comes from
- **Suggested next step:** what to check or change first

Rules: use only the evidence given; never invent exercise names, quotes or numbers;
if a cause is a guess, say so. Output only the markdown."""


def report_markdown_header(repo: str, feature: str, rubric_ver: str, batch: str, runs: list[dict]) -> str:
    """Score tables come from the store, not the LLM, so the numbers in the report are exact."""
    passed = sum(r["verdict"] == "PASS" for r in runs)
    mean = sum(r["overall"] for r in runs) / len(runs)
    dim_names = list(dict.fromkeys(d["dimension"] for r in runs for d in r["dimensions"]))
    lines = [
        f"# Eval report — {repo} / {feature} {rubric_ver}",
        "",
        f"Batch `{batch}` · {runs[0]['created_at'][:16]} · **{passed}/{len(runs)} PASS** · mean overall {mean:.2f}",
        "",
        f"Versions: eval-agent {__version__} · rubric {rubric_ver} · analyze prompt {hashlib.sha256(ANALYZE_PROMPT.encode()).hexdigest()[:12]}",
        "",
        "> Status: PENDING REVIEW (CP4). Edit this file as needed, then `eval-agent report approve <id>`.",
        "",
        "## Scores",
        "",
        "| Item | Verdict | Overall | " + " | ".join(dim_names) + " |",
        "|---|---|---|" + "---|" * len(dim_names),
    ]
    for r in runs:
        scores = {d["dimension"]: d["score"] for d in r["dimensions"]}
        lines.append(
            f"| {r['config_name'] or r['id'][:8]} | {r['verdict']} | {r['overall']:.2f} | "
            + " | ".join(f"{scores[n]:.0f}" if n in scores else "-" for n in dim_names) + " |"
        )
    lines += ["", "| Dimension | Avg /10 |", "|---|---|"]
    for n in dim_names:
        vals = [d["score"] for r in runs for d in r["dimensions"] if d["dimension"] == n]
        lines.append(f"| {n} | {sum(vals) / len(vals):.1f} |")
    return "\n".join(lines)


@main.command("analyze")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--batch", help="Batch ID (default: latest for this feature)")
@click.option("--max-score", default=7.0, show_default=True, help="Only dimensions scoring below this feed the analysis")
@click.option("--context", help="Facts about how the feature works (text, or @file); default <repo>/eval/context/<feature>.md")
@click.option("--model", envvar=["JUDGE_MODEL", "LLM_MODEL_NAME"], default=DEFAULT_MODEL, show_default=True)
@click.option("--base-url", envvar=["JUDGE_BASE_URL", "LLM_BASE_URL"])
@click.option("--api-key", envvar=["JUDGE_API_KEY", "LLM_API_KEY"], default="not-needed")
def analyze_cmd(repo: str, feature: str, batch: str | None, max_score: float, context: str | None,
                model: str, base_url: str, api_key: str):
    """Turn a batch's judge findings into a fix-suggestion report that waits for review (CP4)."""
    from openai import OpenAI

    store = get_store()
    batch = batch or store.latest_batch(repo, feature)
    runs = store.batch_runs(batch) if batch else []
    if not runs:
        console.print(f"[red]No saved runs for {repo}/{feature}[/red]")
        return
    rubric_ver = runs[0]["rubric_ver"]
    if context and context.startswith("@"):
        context = Path(context[1:]).read_text()
    elif not context:
        context_path = get_registry().resolve_context_path(repo, feature)
        if context_path.exists():
            context = context_path.read_text()
            console.print(f"[dim]Using feature context {context_path}[/dim]")

    evidence = []
    for r in runs:
        weak = [d for d in r["dimensions"] if d["score"] < max_score]
        if not weak:
            continue
        evidence.append(f"[{r['config_name'] or r['id'][:8]}] {r['persona'] or ''} overall {r['overall']:.2f}")
        for d in sorted(weak, key=lambda d: d["score"]):
            evidence.append(f"  {d['dimension']} = {d['score']:.0f}: {(d['rationale'] or '')[:600]}")
            if d["evidence"]:
                evidence.append(f"    evidence: {d['evidence'][:500]}")
    header = report_markdown_header(repo, feature, rubric_ver, batch, runs)

    if evidence:
        console.print(f"Analysing {len(runs)} runs from batch {batch}...")
        client = OpenAI(base_url=base_url, api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": ANALYZE_PROMPT.format(
                repo=repo, feature=feature, rubric_ver=rubric_ver, evidence="\n".join(evidence),
                context=f"\nHow the feature works (trust this over your own assumptions):\n{context}\n" if context else "")}],
            temperature=0.2,
            max_tokens=4096,
        )
        issues = (response.choices[0].message.content or "").strip()
    else:
        issues = "## Issues\n\nNo dimension scored below the threshold — nothing to fix."

    reports_dir = HOME / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{repo}-{feature}-{batch}.md"
    path.write_text(header + "\n\n" + issues + "\n")
    report_id = store.create_report(repo, feature, rubric_ver, batch, str(path))
    store.close()

    console.print(f"[green]Report {report_id} written[/green]")
    console.print(f"\n[bold yellow]⏸  CP4 — Review the fix suggestions (waiting for you)[/bold yellow]")
    console.print(f"  File: {path}")
    console.print("  Drop anything wrong, fix wording, add context. Nothing is sent until you approve.")
    console.print(f"  When it is right: eval-agent report approve {report_id}")


@main.group()
def report():
    """Review, approve and send eval reports (CP4)."""
    pass


@report.command("list")
@click.option("--repo")
@click.option("--feature")
def report_list(repo: str | None, feature: str | None):
    """List reports and their review status."""
    store = get_store()
    table = Table(title="Reports")
    for col in ("Id", "Repo/Feature", "Batch", "Status", "Approved by", "Sent to", "File"):
        table.add_column(col)
    for r in store.list_reports(repo, feature):
        table.add_row(r["id"], f"{r['repo']}/{r['feature']}", r["batch_id"], r["status"],
                      r["approved_by"] or "-", r["sent_to"] or "-", r["path"])
    console.print(table)
    store.close()


@report.command("show")
@click.argument("report_id")
def report_show(report_id: str):
    """Print a report."""
    store = get_store()
    r = store.get_report(report_id)
    store.close()
    if not r:
        console.print(f"[red]No report {report_id}[/red]")
        return
    console.print(f"[dim]{r['path']} · {r['status']}[/dim]\n")
    print(Path(r["path"]).read_text())


@report.command("approve")
@click.argument("report_id")
@click.option("--by", help="Reviewer name (default: EVAL_AGENT_USER, git user.name, or OS user)")
def report_approve(report_id: str, by: str | None):
    """Pass CP4: mark the report (as it is now on disk) as reviewed and sendable."""
    store = get_store()
    r = store.get_report(report_id)
    if not r:
        console.print(f"[red]No report {report_id}[/red]")
        return
    path = Path(r["path"])
    text = path.read_text().replace(
        "> Status: PENDING REVIEW (CP4). Edit this file as needed, then `eval-agent report approve <id>`.",
        f"> Status: APPROVED by {reviewer_name(by)} at {datetime.now().isoformat(timespec='seconds')}.",
    )
    path.write_text(text)
    store.approve_report(report_id, reviewer_name(by), hashlib.sha256(text.encode()).hexdigest())
    store.close()
    console.print(f"[green]✅ CP4 passed — report {report_id} approved[/green]")
    console.print(f"  Send it: eval-agent report send {report_id} --discord")


def discord_chunks(text: str, limit: int = 1900) -> list[str]:
    chunks, current = [], ""
    for line in text.splitlines():
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


@report.command("send")
@click.argument("report_id")
@click.option("--discord", "to_discord", is_flag=True, help="Post to DISCORD_WEBHOOK_URL")
def report_send(report_id: str, to_discord: bool):
    """Send an approved report. Refuses unless CP4 passed and the file is unchanged since approval."""
    import httpx

    store = get_store()
    r = store.get_report(report_id)
    if not r:
        console.print(f"[red]No report {report_id}[/red]")
        return
    text = Path(r["path"]).read_text()
    if r["status"] == "pending_review":
        console.print(f"[red]⛔ Report {report_id} has not passed CP4 — review it, then `eval-agent report approve {report_id}`[/red]")
        return
    if r["content_hash"] and hashlib.sha256(text.encode()).hexdigest() != r["content_hash"]:
        console.print(f"[red]⛔ {r['path']} changed after approval — approve it again before sending[/red]")
        return
    if not to_discord:
        console.print("[red]Choose a destination: --discord[/red]")
        return
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        console.print("[red]DISCORD_WEBHOOK_URL is not set (add it to .env)[/red]")
        return
    for chunk in discord_chunks(text):
        httpx.post(webhook, json={"content": chunk}, timeout=30).raise_for_status()
    store.mark_report_sent(report_id, "discord")
    store.close()
    console.print(f"[green]Report {report_id} sent to Discord[/green]")


# ─── Judge reliability: calibration against humans, stability across runs ───


def batch_or_latest(store: Store, repo: str, feature: str, batch: str | None) -> tuple[str | None, list[dict]]:
    batch = batch or store.latest_batch(repo, feature)
    runs = store.batch_runs(batch) if batch else []
    if not runs:
        console.print(f"[red]No saved runs for {repo}/{feature}[/red]")
    return batch, runs


@main.group()
def calibrate():
    """Check the judge against human scores (export sheet → label → import → report)."""
    pass


@calibrate.command("export")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--batch", help="Batch ID (default: latest)")
@click.option("--only", multiple=True, help="Only these items (repeatable)")
def calibrate_export(repo: str, feature: str, batch: str | None, only: tuple[str, ...]):
    """Write a blind labelling sheet: item transcripts + scoring guide + labels.csv (no judge scores)."""
    from eval_agent.calibration import export_sheet

    store = get_store()
    batch, runs = batch_or_latest(store, repo, feature, batch)
    store.close()
    if not runs:
        return
    if only:
        runs = [r for r in runs if r["config_name"] in only]
    rubric_path = get_registry().resolve_rubric_path(repo, feature, runs[0]["rubric_ver"])
    out_dir = HOME / "calibration" / f"{repo}-{feature}-{batch}"
    sheet = export_sheet(out_dir, runs, rubric_path)
    console.print(f"[green]Sheet for {len(runs)} items written to {out_dir}[/green]")
    console.print("\n[bold yellow]⏸  Your part — label by hand (blind to the judge)[/bold yellow]")
    console.print(f"  Read {out_dir / 'GUIDE.md'} and the files in {out_dir / 'items'}")
    console.print(f"  Fill human_score (0-10) in {sheet}; 10-15 items is enough to start")
    console.print(f"  Then: eval-agent calibrate import {sheet}")


@calibrate.command("import")
@click.argument("sheet", type=click.Path(exists=True))
@click.option("--by", help="Labeler name (default: EVAL_AGENT_USER, git user.name, or OS user)")
def calibrate_import(sheet: str, by: str | None):
    """Load human scores from a filled labels.csv."""
    from eval_agent.calibration import read_sheet

    rows, problems = read_sheet(Path(sheet))
    for p in problems:
        console.print(f"  [red]skipped {p}[/red]")
    labeler = reviewer_name(by)
    store = get_store()
    for r in rows:
        store.save_human_label(r["run_id"], r["dimension"], r["score"], labeler, r["note"])
    store.commit()
    store.close()
    console.print(f"[green]{len(rows)} human scores saved for {labeler}[/green]")
    if rows:
        console.print("  Next: eval-agent calibrate report --repo <repo> --feature <feature>")


@calibrate.command("report")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--batch", help="Batch ID (default: latest)")
@click.option("--labeler", help="Only this labeler's scores (default: all labelers, averaged)")
def calibrate_report(repo: str, feature: str, batch: str | None, labeler: str | None):
    """Judge vs human agreement per dimension, and on the final PASS/FAIL."""
    from eval_agent.calibration import TRUST_MAE, TRUST_SPEARMAN, TRUST_WITHIN2, agreement
    from eval_agent.rubric_loader import load_rubric

    store = get_store()
    batch, runs = batch_or_latest(store, repo, feature, batch)
    if not runs:
        return
    labels = store.human_labels([r["id"] for r in runs])
    store.close()
    if labeler:
        labels = [l for l in labels if l["labeler"] == labeler]
    labelers = sorted({l["labeler"] for l in labels})
    if not labels:
        console.print(f"[yellow]No human labels for batch {batch} — start with `eval-agent calibrate export`[/yellow]")
        return

    human: dict[tuple[str, str], list[float]] = {}
    for l in labels:
        human.setdefault((l["run_id"], l["dimension"]), []).append(l["score"])
    judge = {(r["id"], d["dimension"]): d["score"] for r in runs for d in r["dimensions"]}
    weights = {d["dimension"]: d["weight"] for r in runs for d in r["dimensions"]}

    table = Table(title=f"Judge vs reference — {repo}/{feature} batch {batch}\nreference labeler(s): {', '.join(labelers)}")
    for col in ("Dimension", "n", "MAE", "within ±2", "bias (judge−human)", "Spearman", "Trusted"):
        table.add_column(col, justify="right" if col not in ("Dimension", "Trusted") else "left")
    summary = {}
    for dim in weights:
        keys = [k for k in human if k[1] == dim and k in judge]
        if not keys:
            continue
        a = agreement([judge[k] for k in keys], [statistics.mean(human[k]) for k in keys])
        summary[dim] = a
        rho = "n/a" if a["spearman"] is None else f"{a['spearman']:.2f}"
        table.add_row(dim, str(a["n"]), f"{a['mae']:.1f}", f"{a['within2']:.0%}", f"{a['bias']:+.1f}", rho,
                      "[green]yes[/green]" if a["trusted"] else "[red]no[/red]")
    console.print(table)

    rubric = load_rubric(get_registry().resolve_rubric_path(repo, feature, runs[0]["rubric_ver"]))
    max_weighted = sum(10.0 * w for w in weights.values())
    verdicts = []
    for r in runs:
        if all((r["id"], d) in human for d in weights):
            h_overall = sum(statistics.mean(human[(r["id"], d)]) * w for d, w in weights.items()) / max_weighted
            verdicts.append((r["config_name"], r["verdict"], "PASS" if h_overall >= rubric.pass_threshold else "FAIL"))
    if verdicts:
        same = sum(j == h for _, j, h in verdicts)
        console.print(f"\nPASS/FAIL agreement: {same}/{len(verdicts)} fully labelled items")
        for item, j, h in verdicts:
            if j != h:
                console.print(f"  [yellow]{item}: judge {j}, human {h}[/yellow]")

    untrusted = [d for d, a in summary.items() if not a["trusted"]]
    console.print(f"\n[dim]Trusted = within ±2 ≥ {TRUST_WITHIN2:.0%}, MAE ≤ {TRUST_MAE}, Spearman ≥ {TRUST_SPEARMAN}[/dim]")
    if untrusted:
        console.print(f"[bold]Not trusted yet:[/bold] {', '.join(untrusted)} — tighten their rubric wording or the judge prompt, then re-check")
    suffix = f"-{labeler}" if labeler else ""
    out = HOME / "calibration" / f"{repo}-{feature}-{batch}" / f"agreement{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"batch": batch, "labelers": labelers, "dimensions": summary,
                               "verdicts": [dict(zip(("item", "judge", "human"), v)) for v in verdicts]}, indent=2))
    console.print(f"[dim]Saved {out}[/dim]")


@calibrate.command("try")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--version", "rubric_version", required=True, help="Rubric version to try (its draft is used if not approved yet)")
@click.option("--batch", help="Batch whose stored transcripts are re-judged (default: latest)")
@click.option("--labeler", help="Reference labels to compare against (default: all)")
@click.option("--runs", default=3, show_default=True)
@click.option("--concurrency", default=4, show_default=True)
@click.option("--model", envvar=["JUDGE_MODEL", "LLM_MODEL_NAME"], default=DEFAULT_MODEL, show_default=True)
@click.option("--base-url", envvar=["JUDGE_BASE_URL", "LLM_BASE_URL"])
@click.option("--api-key", envvar=["JUDGE_API_KEY", "LLM_API_KEY"], default="not-needed")
def calibrate_try(repo: str, feature: str, rubric_version: str, batch: str | None, labeler: str | None, runs: int,
                  concurrency: int, model: str, base_url: str, api_key: str):
    """Before approving a rubric: re-judge labelled transcripts with it and compare agreement with the current one.

    Nothing is saved to the run history; results go to calibration/<...>/try-<version>.json.
    """
    from eval_agent.calibration import agreement
    from eval_agent.judge import judge_transcript

    reg = get_registry()
    rubric_path = reg.resolve_rubric_path(repo, feature, rubric_version)
    if not rubric_path.exists():
        rubric_path = rubric_path.with_suffix(".draft.yaml")
    if not rubric_path.exists():
        console.print(f"[red]No rubric or draft for {rubric_version}[/red]")
        return
    store = get_store()
    batch, stored = batch_or_latest(store, repo, feature, batch)
    labels = store.human_labels([r["id"] for r in stored]) if stored else []
    store.close()
    if labeler:
        labels = [l for l in labels if l["labeler"] == labeler]
    if not labels:
        console.print("[red]No reference labels for this batch — run calibrate export/import first[/red]")
        return
    ref: dict[tuple[str, str], list[float]] = {}
    for l in labels:
        ref.setdefault((l["run_id"], l["dimension"]), []).append(l["score"])
    labelled = [r for r in stored if any(k[0] == r["id"] for k in ref)]
    console.print(f"Re-judging {len(labelled)} labelled transcripts with {rubric_path.name} × {runs} runs (not saved)...")

    async def _run():
        sem = asyncio.Semaphore(concurrency)

        async def one(r: dict):
            async with sem:
                try:
                    res = await judge_transcript(json.loads(r["transcript"]), rubric_path, model=model,
                                                 base_url=base_url, api_key=api_key, runs=runs)
                except Exception as e:
                    console.print(f"  [red]{r['config_name']}: {e}[/red]")
                    return None
            console.print(f"  [dim]done {r['config_name'] or r['id'][:8]}[/dim]")
            return r["id"], {d.name: d.score for d in res.dimensions}

        return [x for x in await asyncio.gather(*[one(r) for r in labelled]) if x]

    new_scores = dict(asyncio.run(_run()))
    old_scores = {r["id"]: {d["dimension"]: d["score"] for d in r["dimensions"]} for r in labelled}

    table = Table(title=f"Agreement with reference: {stored[0]['rubric_ver']} (stored) vs {rubric_version} (try)\n"
                        f"reference: {', '.join(sorted({l['labeler'] for l in labels}))}")
    for col in ("Dimension", "MAE old→new", "within ±2 old→new", "bias old→new", "Spearman old→new", "Trusted old→new"):
        table.add_column(col)
    result = {}
    for dim in dict.fromkeys(k[1] for k in ref):
        keys = [k for k in ref if k[1] == dim and k[0] in new_scores and dim in new_scores[k[0]] and dim in old_scores.get(k[0], {})]
        if not keys:
            continue
        human = [statistics.mean(ref[k]) for k in keys]
        a = agreement([old_scores[k[0]][dim] for k in keys], human)
        b = agreement([new_scores[k[0]][dim] for k in keys], human)
        result[dim] = {"old": a, "new": b}
        rho = lambda x: "n/a" if x is None else f"{x:.2f}"
        yes = lambda t: "yes" if t else "no"
        table.add_row(dim, f"{a['mae']:.1f} → {b['mae']:.1f}", f"{a['within2']:.0%} → {b['within2']:.0%}",
                      f"{a['bias']:+.1f} → {b['bias']:+.1f}", f"{rho(a['spearman'])} → {rho(b['spearman'])}",
                      f"{yes(a['trusted'])} → {yes(b['trusted'])}")
    console.print(table)
    console.print("[dim]Caveat: tuning a rubric on the same items it is checked on overstates the gain; confirm on a new batch.[/dim]")
    out = HOME / "calibration" / f"{repo}-{feature}-{batch}" / f"try-{rubric_version}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"batch": batch, "rubric": str(rubric_path), "runs": runs, "agreement": result,
                               "scores": new_scores}, indent=2))
    console.print(f"[dim]Saved {out}[/dim]")


@main.command("stability")
@click.option("--repo", required=True)
@click.option("--feature", required=True)
@click.option("--batch", help="Batch ID whose stored transcripts are re-judged (default: latest)")
@click.option("--only", multiple=True, help="Only these items (repeatable)")
@click.option("--runs", default=5, show_default=True, help="Judge runs per transcript")
@click.option("--concurrency", default=4, show_default=True)
@click.option("--model", envvar=["JUDGE_MODEL", "LLM_MODEL_NAME"], default=DEFAULT_MODEL, show_default=True)
@click.option("--base-url", envvar=["JUDGE_BASE_URL", "LLM_BASE_URL"])
@click.option("--api-key", envvar=["JUDGE_API_KEY", "LLM_API_KEY"], default="not-needed")
def stability_cmd(repo: str, feature: str, batch: str | None, only: tuple[str, ...], runs: int, concurrency: int,
                  model: str, base_url: str, api_key: str):
    """Re-judge stored transcripts several times; the input is fixed, so any spread is the judge's own noise."""
    from eval_agent.calibration import UNSTABLE_RANGE, stability
    from eval_agent.judge import judge_transcript
    from eval_agent.rubric_loader import load_rubric

    store = get_store()
    batch, stored = batch_or_latest(store, repo, feature, batch)
    store.close()
    if not stored:
        return
    if only:
        stored = [r for r in stored if r["config_name"] in only]
    rubric_path = get_registry().resolve_rubric_path(repo, feature, stored[0]["rubric_ver"])
    rubric = load_rubric(rubric_path)
    no_config = hashlib.sha256(b"{}").hexdigest()[:16]
    if any(r["trigger"] == "manual" and r["config_hash"] != no_config for r in stored):
        console.print("[yellow]⚠ Some runs were judged with a --config-file that is not stored; they are re-judged without it[/yellow]")
    console.print(f"Re-judging {len(stored)} transcripts × {runs} runs from batch {batch}...")

    async def _run():
        sem = asyncio.Semaphore(concurrency)

        async def one(r: dict):
            async with sem:
                try:
                    res = await judge_transcript(json.loads(r["transcript"]), rubric_path, model=model,
                                                 base_url=base_url, api_key=api_key, runs=runs)
                except Exception as e:
                    console.print(f"  [red]{r['config_name']}: {e}[/red]")
                    return None
            console.print(f"  [dim]done {r['config_name'] or r['id'][:8]}[/dim]")
            return r["config_name"] or r["id"][:8], {d.name: d.run_scores for d in res.dimensions}

        return [x for x in await asyncio.gather(*[one(r) for r in stored]) if x and x[1]]

    per_item = dict(asyncio.run(_run()))
    result = stability(per_item, {d.name: d.weight for d in rubric.dimensions}, rubric.pass_threshold)

    table = Table(title=f"Judge stability — {runs} runs per transcript, {result['items']} items")
    for col in ("Dimension", "mean std", "mean range", f"items with range ≥ {UNSTABLE_RANGE}"):
        table.add_column(col, justify="right" if col != "Dimension" else "left")
    for dim, st in result["dimensions"].items():
        color = "red" if st["unstable_share"] > 0.3 else "yellow" if st["unstable_share"] > 0.1 else "green"
        table.add_row(dim, f"{st['mean_std']:.2f}", f"{st['mean_range']:.1f}", f"[{color}]{st['unstable_share']:.0%}[/{color}]")
    console.print(table)
    flips = result["verdict_flips"]
    console.print(f"\nPASS/FAIL flips between single runs: {len(flips)}/{result['items']} items" + (f" ({', '.join(flips)})" if flips else ""))
    console.print(f"[dim]The saved score is the median of --runs judge calls; single-call noise above shows why.[/dim]")

    out = HOME / "calibration" / f"{repo}-{feature}-{batch}" / "stability.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"batch": batch, "runs": runs, "model": model, **result, "scores": per_item}, indent=2))
    console.print(f"[dim]Saved {out}[/dim]")


# ─── Agent mode ───


@main.command("agent")
@click.option("--repo", help="Focus on one repo (default: every registered feature)")
@click.option("--feature", help="Focus on one feature")
@click.option("--goal", help="What to do, in plain words (default: routine check)")
@click.option("--max-steps", default=12, show_default=True)
@click.option("--model", envvar=["AGENT_MODEL", "LLM_MODEL_NAME"], default=DEFAULT_MODEL, show_default=True)
@click.option("--base-url", envvar=["AGENT_BASE_URL", "LLM_BASE_URL"])
@click.option("--api-key", envvar=["AGENT_API_KEY", "LLM_API_KEY"], default="not-needed")
def agent_cmd(repo: str | None, feature: str | None, goal: str | None, max_steps: int, model: str, base_url: str, api_key: str):
    """Let the LLM decide what to check and run; it stops at every human checkpoint."""
    from eval_agent.agent import Agent

    target = f"{repo}/{feature}" if repo and feature else (f"repo {repo}" if repo else "every registered feature with an approved rubric")
    goal = goal or (
        f"Routine check of {target}. Find out whether anything changed in the target repo since the last eval batch "
        "or is pending. If it changed, re-evaluate and compare with the baseline; if there are failures or "
        "regressions without an analysis report yet, prepare one. Then report what is waiting on the human."
    )
    if repo and feature:
        goal += f"\n(Target: repo={repo}, feature={feature})"

    agent = Agent(HOME, model, base_url, api_key, max_steps=max_steps,
                  echo=lambda line: console.print(line, style="dim", markup=False, highlight=False))
    result = agent.run(goal)

    console.print("\n[bold]Agent summary[/bold]")
    console.print(result["summary"], markup=False)
    console.print("\n[bold yellow]⏸  Waiting on you (agent's view)[/bold yellow]")
    for item in result["waiting_on_human"] or ["(none reported)"]:
        console.print(f"  • {item}", markup=False)
    console.print(f"\n[dim]{result['steps']} step(s) · full trace: {result['log']}[/dim]")
    console.print("\n[bold]Ground truth from `status`[/bold] (trust this over the agent's wording):")
    ctx = click.get_current_context()
    ctx.invoke(status_cmd, repo=repo)
    console.print("[dim]Checkpoints are only passed by you: approve, baseline set, report approve/send, calibrate import.[/dim]")


# ─── Status: what is waiting on a human ───


@main.command("status")
@click.option("--repo")
def status_cmd(repo: str | None):
    """Show where each feature is in the workflow and what is waiting on you."""
    reg = get_registry()
    store = get_store()
    table = Table(title="Workflow status")
    for col in ("Repo/Feature", "Version", "Stage", "Last batch", "Waiting on you / next"):
        table.add_column(col)
    waiting = 0

    for repo_name, r in reg.repos.items():
        if repo and repo_name != repo:
            continue
        for feat_name, feat in r.features.items():
            ver = feat.active_version or "-"
            v = feat.versions.get(feat.active_version or "")
            todo: list[str] = []
            last = "-"
            if not v:
                stage = "-"
                todo.append(f"next: spec pipeline --repo {repo_name} --feature {feat_name} --version <v> --po-file <spec>")
            elif v.status != Status.APPROVED and not v.stage and not any(
                p.exists() for p in (reg.resolve_rubric_path(repo_name, feat_name),
                                     reg.resolve_rubric_path(repo_name, feat_name).with_suffix(".draft.yaml"))):
                stage = "no rubric"
                todo.append(f"next: spec pipeline --repo {repo_name} --feature {feat_name} --version {ver} --po-file <spec>")
            elif v.status != Status.APPROVED:
                pending = CHECKPOINTS.get(v.stage) if v.stage else (3, "Review the rubric")
                stage = v.stage.value if v.stage else "draft"
                todo.append(f"[yellow]YOU: CP{pending[0]} {pending[1]}[/yellow] → eval-agent approve --repo {repo_name} --feature {feat_name}")
                waiting += 1
            else:
                stage = "approved"
                batch = store.latest_batch(repo_name, feat_name)
                if not batch:
                    todo.append("next: run / eval")
                else:
                    runs = store.batch_runs(batch)
                    passed = sum(x["verdict"] == "PASS" for x in runs)
                    mean = sum(x["overall"] for x in runs) / len(runs)
                    last = f"{runs[0]['created_at'][:10]} {passed}/{len(runs)} PASS · mean {mean:.2f}"
                    if runs[0]["rubric_ver"] != feat.active_version:
                        todo.append(f"next: last batch used rubric {runs[0]['rubric_ver']}, active is {feat.active_version} → run --repo {repo_name} --feature {feat_name}")
                    if not store.batch_is_baseline(batch):
                        todo.append(f"[yellow]YOU: accept as baseline?[/yellow] → baseline set --batch {batch}")
                        waiting += 1
                    all_reports = store.list_reports(repo_name, feat_name)
                    # Pending reviews stay visible even after a newer batch runs
                    for rep in all_reports:
                        if rep["status"] == "pending_review":
                            todo.append(f"[yellow]YOU: CP4 review report[/yellow] → report approve {rep['id']}  (batch {rep['batch_id']})")
                            waiting += 1
                        elif rep["status"] == "approved":
                            todo.append(f"optional: report send {rep['id']} --discord")
                    if passed < len(runs) and not any(x["batch_id"] == batch for x in all_reports):
                        todo.append(f"next: analyze --repo {repo_name} --feature {feat_name}")
                    if not store.has_human_labels(repo_name, feat_name):
                        todo.append(f"next: check the judge against your own scores → calibrate export --repo {repo_name} --feature {feat_name}")
            for pname, pv in reg.pending_versions(repo_name, feat_name).items():
                pending = CHECKPOINTS.get(pv.stage) if pv.stage else (3, "Review the rubric")
                todo.append(f"[yellow]YOU: CP{pending[0]} {pending[1]} for {pname}[/yellow] → eval-agent approve --repo {repo_name} --feature {feat_name} --version {pname}")
                waiting += 1
            table.add_row(f"{repo_name}/{feat_name}", ver, stage, last, "\n".join(todo) or "[green]nothing pending[/green]")

    store.close()
    console.print(table)
    console.print(f"\n[bold]{waiting} item(s) waiting on you[/bold]")


if __name__ == "__main__":
    main()
