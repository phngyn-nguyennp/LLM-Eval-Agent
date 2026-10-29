"""Where eval-agent keeps its state: registry.yaml, eval.db, .env and reports/."""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_HOME = Path.home() / ".eval-agent"

ENV_TEMPLATE = """# OpenAI-compatible endpoint used for spec generation and judging
LLM_HOST=http://localhost
LLM_PORT=8000
LLM_API_KEY=
LLM_MODEL_NAME=Apero-LLM

# Optional: a separate judge endpoint/model for `eval` and `run`
# JUDGE_BASE_URL=
# JUDGE_API_KEY=
# JUDGE_MODEL=

# Optional: Discord webhook for `report send`
# DISCORD_WEBHOOK_URL=
"""


def get_home() -> Path:
    """EVAL_AGENT_HOME, else the current directory if it holds a registry, else ~/.eval-agent."""
    env = os.environ.get("EVAL_AGENT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if (Path.cwd() / "registry.yaml").exists():
        return Path.cwd()
    return DEFAULT_HOME


def load_env(home: Path | None = None):
    """Load <home>/.env (without overriding the shell) and derive LLM_BASE_URL from LLM_HOST/LLM_PORT."""
    path = (home or get_home()) / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))

    host = os.environ.get("LLM_HOST")
    if host and not os.environ.get("LLM_BASE_URL"):
        if "://" not in host:
            host = f"http://{host}"
        port = os.environ.get("LLM_PORT")
        os.environ["LLM_BASE_URL"] = f"{host}:{port}/v1" if port else f"{host}/v1"


def init_home(home: Path) -> list[str]:
    """Create the home layout; never overwrites existing files. Returns the paths created."""
    created = []
    home.mkdir(parents=True, exist_ok=True)
    (home / "reports").mkdir(exist_ok=True)
    for name, content in (("registry.yaml", "repos: {}\n"), (".env", ENV_TEMPLATE)):
        path = home / name
        if not path.exists():
            path.write_text(content)
            created.append(str(path))
    return created
