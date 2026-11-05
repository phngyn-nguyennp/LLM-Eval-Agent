"""Agent mode: an LLM decides which eval-agent tools to call, and stops at every human checkpoint.

The LLM only gets the tools in TOOLS. Anything that passes a checkpoint (approve, baseline set,
report approve/send, calibrate import) is not a tool, so the agent cannot do it — it can only
prepare the work and say what is waiting on the human.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from openai import OpenAI

from eval_agent import __version__
from eval_agent.registry import Registry
from eval_agent.store import Store

MAX_TOOL_OUTPUT = 6000
EXPENSIVE = {"run_eval": 1, "check_stability": 1}  # calls allowed per session

SYSTEM_PROMPT = """You are eval-agent, an AI evaluation engineer. You keep LLM features under
continuous evaluation using the tools you are given.

How you work:
- Start by looking at the workflow status and what changed in the target repo since the last eval.
- Do only the work that is needed. If nothing changed since the last batch and nothing is pending,
  say so and finish; do not re-run evals for no reason.
- run_eval and check_stability are slow and costly: at most once per session each, and only when
  the target repo changed, there is no batch yet, or you have a concrete reason.
- After a run, use compare_batches to see what changed against the previous batch. If it warns that
  the rubric or judge changed, say the comparison is not like-for-like instead of claiming a regression.
- After a run with failures or regressions, call analyze so a fix-suggestion report is ready.
- You cannot approve rubrics, set baselines, approve or send reports, or import human labels.
  Those are human checkpoints. Never claim you did them; list them as waiting on the human.
- Base every statement on tool output. Quote numbers exactly as the tools print them; never compute
  your own averages or totals — get_status shows each batch's pass count and mean.
- waiting_on_human must include every "YOU:" item that get_status shows, plus any "next:" step you
  recommend the human take. Copy each command exactly as get_status prints it; never invent flags.
- Use list_items for item ids before passing `only`; never guess ids.
- Call get_status again right before finish, so the waiting list reflects what you just did.
- When done, call finish with a short summary and the list of decisions waiting on the human."""

TOOLS = [
    {"name": "get_status", "description": "Workflow status of every feature: stage, last batch, what waits on the human.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "repo_changes", "description": "Read-only git view of the target repo: commits since the last eval batch, uncommitted files, diff stat.",
     "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "feature": {"type": "string"}}, "required": ["repo", "feature"]}},
    {"name": "list_items", "description": "Item ids (e.g. profile ids) in the latest batch of a feature; use these exact ids for `only`.",
     "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "feature": {"type": "string"}}, "required": ["repo", "feature"]}},
    {"name": "history", "description": "Recent saved eval runs for a feature, newest first.",
     "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "feature": {"type": "string"}}, "required": ["repo", "feature"]}},
    {"name": "run_eval", "description": "Generate outputs with the feature's adapter and judge them (new batch, compared with baselines). Slow.",
     "parameters": {"type": "object", "properties": {
         "repo": {"type": "string"}, "feature": {"type": "string"},
         "only": {"type": "array", "items": {"type": "string"}, "description": "Optional item ids to limit the run"},
         "runs": {"type": "integer", "description": "Judge runs per item, 1-3 (default 3)"}},
         "required": ["repo", "feature"]}},
    {"name": "compare_batches", "description": "Compare the latest batch with the one before it, item by item; flags when rubric/judge versions differ (then scores are not like-for-like).",
     "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "feature": {"type": "string"}}, "required": ["repo", "feature"]}},
    {"name": "analyze", "description": "Turn the latest batch's findings into a fix-suggestion report that waits for human review (CP4).",
     "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "feature": {"type": "string"}}, "required": ["repo", "feature"]}},
    {"name": "check_stability", "description": "Re-judge stored transcripts of the latest batch several times to measure judge noise. Slow.",
     "parameters": {"type": "object", "properties": {
         "repo": {"type": "string"}, "feature": {"type": "string"},
         "only": {"type": "array", "items": {"type": "string"}}, "runs": {"type": "integer", "description": "3-5"}},
         "required": ["repo", "feature"]}},
    {"name": "calibration_report", "description": "Judge vs human agreement for the latest batch (needs human labels).",
     "parameters": {"type": "object", "properties": {"repo": {"type": "string"}, "feature": {"type": "string"}}, "required": ["repo", "feature"]}},
    {"name": "read_file", "description": "Read a text file (or list a directory) inside a registered repo or the eval-agent home. Eval results live in the database, not in files: use get_status, history, list_items for them. Read-only.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "finish", "description": "End the session.",
     "parameters": {"type": "object", "properties": {
         "summary": {"type": "string", "description": "What you found and did, with exact numbers"},
         "waiting_on_human": {"type": "array", "items": {"type": "string"}, "description": "Decisions or checkpoints the human must handle, with the command to run"}},
         "required": ["summary", "waiting_on_human"]}},
]


class Agent:
    def __init__(self, home: Path, model: str, base_url: str | None, api_key: str, max_steps: int = 12, echo=print):
        self.home = home
        self.model = model
        from eval_agent.access import guard_llm

        if guard_llm(base_url):  # tool results carry plans and transcripts: keep the agent on internal endpoints
            from eval_agent.access import ExternalEndpointRefused
            raise ExternalEndpointRefused("agent mode only runs on an internal LLM endpoint (internal_hosts in access.yaml)")
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.max_steps = max_steps
        self.echo = echo
        self.used: dict[str, int] = {}
        self.log: list[str] = []

    # ─── tools ───

    def _cli(self, *args: str, timeout: int = 1800) -> str:
        pkg_root = str(Path(__file__).resolve().parent.parent)
        env = {**os.environ, "EVAL_AGENT_HOME": str(self.home), "COLUMNS": "200", "NO_COLOR": "1", "TERM": "dumb",
               "PYTHONPATH": os.pathsep.join(filter(None, [pkg_root, os.environ.get("PYTHONPATH")]))}
        proc = subprocess.run([sys.executable, "-m", "eval_agent.cli", *args], capture_output=True, text=True,
                              env=env, timeout=timeout)
        out = (proc.stdout + proc.stderr).strip()
        return out if proc.returncode == 0 else f"[exit {proc.returncode}]\n{out}"

    def _registry(self) -> Registry:
        return Registry(self.home / "registry.yaml")

    def _repo_changes(self, repo: str, feature: str) -> str:
        path = self._registry().repo_path(repo)
        store = Store(self.home / "eval.db")
        batch = store.latest_batch(repo, feature)
        since = store.batch_runs(batch)[0]["created_at"] if batch else None
        store.close()

        def git(*a: str) -> str:
            p = subprocess.run(["git", "-C", str(path), *a], capture_output=True, text=True)
            return (p.stdout or p.stderr).strip() or "(none)"

        parts = [f"Target repo: {path}", f"Last eval batch: {batch or 'none'} at {since or '-'} (UTC)"]
        log_args = ["log", "--oneline", "-n", "20"] + ([f"--since={since}Z"] if since else [])
        parts += ["Commits since last batch:", git(*log_args)]
        parts += ["Uncommitted changes (tracked files):", git("diff", "--stat", "HEAD")]
        parts += ["Untracked files:", git("ls-files", "--others", "--exclude-standard")[:1500]]
        return "\n".join(parts)

    def _read_file(self, path: str) -> str:
        p = Path(path).expanduser().resolve()
        reg = self._registry()
        allowed = [self.home.resolve()] + [reg.repo_path(r) for r in reg.repos]
        if not any(p == a or a in p.parents for a in allowed):
            return f"Refused: {p} is outside the registered repos and the eval-agent home."
        if p.is_dir():
            entries = sorted(p.iterdir())[:200]
            return "\n".join(f"{e.name}/" if e.is_dir() else e.name for e in entries) or "(empty directory)"
        if not p.is_file():
            return f"Not found: {p}"
        return p.read_text(errors="replace")

    def _items(self, repo: str, feature: str) -> list[str]:
        store = Store(self.home / "eval.db")
        batch = store.latest_batch(repo, feature)
        runs = store.batch_runs(batch) if batch else []
        store.close()
        return [r["config_name"] for r in runs if r["config_name"]]

    def call_tool(self, name: str, args: dict) -> str:
        if name in ("run_eval", "check_stability") and args.get("only"):
            known = self._items(args.get("repo", ""), args.get("feature", ""))
            unknown = [i for i in args["only"] if known and i not in known]
            if unknown:
                # Checked before the budget is spent, so a typo costs nothing
                return f"Unknown item id(s) {unknown}. Valid ids: {', '.join(known)}"
        if name in EXPENSIVE:
            if self.used.get(name, 0) >= EXPENSIVE[name]:
                return f"Refused: {name} was already used {EXPENSIVE[name]} time(s) this session."
            self.used[name] = self.used.get(name, 0) + 1
        repo, feature = args.get("repo", ""), args.get("feature", "")
        only = [a for item in args.get("only") or [] for a in ("--only", str(item))]
        if name == "get_status":
            return self._cli("status")
        if name == "repo_changes":
            return self._repo_changes(repo, feature)
        if name == "list_items":
            items = self._items(repo, feature)
            return ", ".join(items) if items else "No batch yet for this feature."
        if name == "history":
            return self._cli("history", repo, "--feature", feature, "--limit", "20")
        if name == "run_eval":
            runs = str(min(max(int(args.get("runs") or 3), 1), 3))
            return self._cli("run", "--repo", repo, "--feature", feature, "--runs", runs, *only)
        if name == "compare_batches":
            return self._cli("compare", "--repo", repo, "--feature", feature)
        if name == "analyze":
            return self._cli("analyze", "--repo", repo, "--feature", feature)
        if name == "check_stability":
            runs = str(min(max(int(args.get("runs") or 3), 3), 5))
            return self._cli("stability", "--repo", repo, "--feature", feature, "--runs", runs, *only)
        if name == "calibration_report":
            return self._cli("calibrate", "report", "--repo", repo, "--feature", feature)
        if name == "read_file":
            return self._read_file(args.get("path", ""))
        return f"Unknown tool {name}"

    # ─── loop ───

    def run(self, goal: str) -> dict:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": goal}]
        tools = [{"type": "function", "function": t} for t in TOOLS]
        prompt_hash = hashlib.sha256((SYSTEM_PROMPT + json.dumps(TOOLS)).encode()).hexdigest()[:12]
        self._log(f"# Agent session {datetime.now().isoformat(timespec='seconds')}\n\n"
                  f"Model: {self.model} · eval-agent {__version__} · agent prompt+tools {prompt_hash}\n\n## Goal\n\n{goal}\n")

        for step in range(1, self.max_steps + 1):
            final_step = step == self.max_steps
            if final_step:
                messages.append({"role": "user", "content": "Step limit reached: call finish now with what you have."})
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.1,
                max_tokens=2048,
            )
            msg = response.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))
            if msg.content:
                self._log(f"## Step {step} · thinking\n\n{msg.content}\n")
                self.echo(f"[thinking] {msg.content.strip()[:400]}")
            if not msg.tool_calls:
                messages.append({"role": "user", "content": "Use a tool, or call finish if you are done."})
                continue

            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                name = call.function.name
                if name == "finish":
                    self._log(f"## Finish\n\n{args.get('summary', '')}\n\n### Waiting on the human\n\n"
                              + "\n".join(f"- {w}" for w in args.get("waiting_on_human", [])) + "\n")
                    return {"summary": args.get("summary", ""), "waiting_on_human": args.get("waiting_on_human", []),
                            "steps": step, "log": self.save_log()}
                self.echo(f"[tool] {name} {json.dumps(args, ensure_ascii=False)}")
                try:
                    result = self.call_tool(name, args)
                except Exception as e:  # a failing tool is information for the agent, not a crash
                    result = f"Tool error: {type(e).__name__}: {e}"
                if len(result) > MAX_TOOL_OUTPUT:
                    result = result[:MAX_TOOL_OUTPUT // 2] + "\n...[truncated]...\n" + result[-MAX_TOOL_OUTPUT // 2:]
                self._log(f"## Step {step} · {name}\n\n```json\n{json.dumps(args, ensure_ascii=False)}\n```\n\n```\n{result}\n```\n")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

        return {"summary": "Stopped: step limit reached without finish.", "waiting_on_human": [],
                "steps": self.max_steps, "log": self.save_log()}

    def _log(self, text: str):
        self.log.append(text)

    def save_log(self) -> str:
        logs = self.home / "agent-logs"
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        path.write_text("\n".join(self.log))
        return str(path)
