from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from eval_agent.home import get_home
from eval_agent.models import DimensionScore, EvalResult, Verdict

DB_FILE = "eval.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    repo        TEXT NOT NULL,
    feature     TEXT NOT NULL,
    rubric_ver  TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config_name TEXT,
    model       TEXT NOT NULL,
    model_version TEXT,
    trigger     TEXT NOT NULL,
    user_level  TEXT,
    persona     TEXT,
    overall     REAL NOT NULL,
    verdict     TEXT NOT NULL,
    auto_fail   INTEGER DEFAULT 0,
    total_turns INTEGER,
    latency_ms  INTEGER,
    judge_model TEXT,
    transcript  TEXT
);

CREATE TABLE IF NOT EXISTS dimension_scores (
    run_id      TEXT REFERENCES eval_runs(id),
    dimension   TEXT NOT NULL,
    score       REAL NOT NULL,
    weight      REAL NOT NULL DEFAULT 1.0,
    rationale   TEXT,
    evidence    TEXT,
    PRIMARY KEY (run_id, dimension)
);

CREATE TABLE IF NOT EXISTS baselines (
    id          TEXT PRIMARY KEY,
    repo        TEXT NOT NULL,
    feature     TEXT NOT NULL,
    rubric_ver  TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    overall     REAL NOT NULL,
    run_id      TEXT REFERENCES eval_runs(id),
    active      INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS reports (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    repo        TEXT NOT NULL,
    feature     TEXT NOT NULL,
    rubric_ver  TEXT NOT NULL,
    batch_id    TEXT NOT NULL,
    path        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending_review',
    approved_by TEXT,
    approved_at TEXT,
    content_hash TEXT,
    sent_to     TEXT,
    sent_at     TEXT
);

CREATE TABLE IF NOT EXISTS human_labels (
    run_id      TEXT NOT NULL REFERENCES eval_runs(id),
    dimension   TEXT NOT NULL,
    score       REAL NOT NULL,
    labeler     TEXT NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (run_id, dimension, labeler)
);

-- One row per CLI command, so how often each step is used is shown by log, not by word (`eval-agent ops`).
-- Only the command path is kept, never its arguments (they can hold keys or tokens).
CREATE TABLE IF NOT EXISTS usage_log (
    at          TEXT NOT NULL,
    command     TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    duration_ms INTEGER,
    tool_version TEXT,
    source      TEXT NOT NULL DEFAULT 'cli'
);

CREATE INDEX IF NOT EXISTS idx_runs_repo ON eval_runs(repo, feature, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_version ON eval_runs(repo, feature, rubric_ver, created_at);
"""


class Store:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or get_home() / DB_FILE)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(eval_runs)")}
        if "batch_id" not in cols:
            # Runs saved before batches existed each become their own batch
            self.conn.execute("ALTER TABLE eval_runs ADD COLUMN batch_id TEXT")
            self.conn.execute("UPDATE eval_runs SET batch_id = id WHERE batch_id IS NULL")
            self.conn.commit()
        for col in ("judge_prompt_hash", "rubric_hash", "tool_version", "input_format"):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE eval_runs ADD COLUMN {col} TEXT")
        dim_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(dimension_scores)")}
        if "run_scores" not in dim_cols:
            self.conn.execute("ALTER TABLE dimension_scores ADD COLUMN run_scores TEXT")
        self.conn.commit()
        # Only one *active* baseline per item; the old UNIQUE(..., active) also blocked a second retired one
        table_sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE name = 'baselines'").fetchone()[0]
        if "UNIQUE" in table_sql:
            self.conn.executescript("""
                ALTER TABLE baselines RENAME TO baselines_old;
                CREATE TABLE baselines (
                    id TEXT PRIMARY KEY, repo TEXT NOT NULL, feature TEXT NOT NULL,
                    rubric_ver TEXT NOT NULL, config_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                    overall REAL NOT NULL, run_id TEXT REFERENCES eval_runs(id), active INTEGER DEFAULT 1);
                INSERT INTO baselines SELECT * FROM baselines_old;
                DROP TABLE baselines_old;
            """)
        self.conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS baselines_one_active
            ON baselines(repo, feature, rubric_ver, config_hash) WHERE active = 1"""
        )
        self.conn.commit()
        report_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(reports)")}
        if "content_hash" not in report_cols:
            self.conn.execute("ALTER TABLE reports ADD COLUMN content_hash TEXT")
            self.conn.commit()

    def close(self):
        self.conn.close()

    def save_eval_run(self, result: EvalResult, batch_id: str | None = None) -> str:
        run_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO eval_runs
            (id, created_at, repo, feature, rubric_ver, config_hash, config_name,
             model, trigger, user_level, persona, overall, verdict, auto_fail,
             total_turns, latency_ms, judge_model, transcript, batch_id, judge_prompt_hash, rubric_hash, tool_version, input_format)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                result.created_at.isoformat(),
                result.repo,
                result.feature,
                result.rubric_ver,
                result.config_hash,
                result.config_name,
                result.model,
                result.trigger,
                result.user_level,
                result.persona,
                result.overall,
                result.verdict.value,
                int(result.auto_fail),
                result.total_turns,
                result.latency_ms,
                result.judge_model,
                json.dumps(result.transcript, ensure_ascii=False),
                batch_id or run_id,
                result.judge_prompt_hash,
                result.rubric_hash,
                result.tool_version,
                result.input_format,
            ),
        )
        for dim in result.dimensions:
            self.conn.execute(
                """INSERT INTO dimension_scores
                (run_id, dimension, score, weight, rationale, evidence, run_scores)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, dim.name, dim.score, dim.weight, dim.rationale, dim.evidence, json.dumps(dim.run_scores)),
            )
        self.conn.commit()
        return run_id

    def get_run(self, run_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        dims = self.conn.execute(
            "SELECT * FROM dimension_scores WHERE run_id = ?", (run_id,)
        ).fetchall()
        result["dimensions"] = [dict(d) for d in dims]
        return result

    def list_runs(
        self,
        repo: str | None = None,
        feature: str | None = None,
        rubric_ver: str | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        query = "SELECT * FROM eval_runs WHERE 1=1"
        params: list = []
        if repo:
            query += " AND repo = ?"
            params.append(repo)
        if feature:
            query += " AND feature = ?"
            params.append(feature)
        if rubric_ver:
            query += " AND rubric_ver = ?"
            params.append(rubric_ver)
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_history(
        self,
        repo: str,
        feature: str,
        rubric_ver: str | None = None,
        limit: int = 30,
    ) -> list[dict]:
        return self.list_runs(repo=repo, feature=feature, rubric_ver=rubric_ver, limit=limit)

    def set_baseline(self, run_id: str) -> str:
        run = self.get_run(run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")
        self.conn.execute(
            """UPDATE baselines SET active = 0
            WHERE repo = ? AND feature = ? AND rubric_ver = ? AND config_hash = ? AND active = 1""",
            (run["repo"], run["feature"], run["rubric_ver"], run["config_hash"]),
        )
        baseline_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO baselines
            (id, repo, feature, rubric_ver, config_hash, created_at, overall, run_id, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                baseline_id,
                run["repo"],
                run["feature"],
                run["rubric_ver"],
                run["config_hash"],
                datetime.utcnow().isoformat(),
                run["overall"],
                run_id,
            ),
        )
        self.conn.commit()
        return baseline_id

    def get_active_baseline(
        self, repo: str, feature: str, rubric_ver: str, config_hash: str
    ) -> dict | None:
        row = self.conn.execute(
            """SELECT * FROM baselines
            WHERE repo = ? AND feature = ? AND rubric_ver = ? AND config_hash = ? AND active = 1""",
            (repo, feature, rubric_ver, config_hash),
        ).fetchone()
        return dict(row) if row else None

    def list_active_baselines(self, repo: str, feature: str, rubric_ver: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT b.*, r.config_name FROM baselines b
            LEFT JOIN eval_runs r ON r.id = b.run_id
            WHERE b.repo = ? AND b.feature = ? AND b.rubric_ver = ? AND b.active = 1
            ORDER BY r.config_name""",
            (repo, feature, rubric_ver),
        ).fetchall()
        return [dict(r) for r in rows]

    def detect_regression(
        self, result: EvalResult, threshold: float = 0.10
    ) -> dict | None:
        baseline = self.get_active_baseline(
            result.repo, result.feature, result.rubric_ver, result.config_hash
        )
        if not baseline:
            return None
        delta = result.overall - baseline["overall"]
        if delta < -threshold:
            baseline_dims = self.conn.execute(
                "SELECT * FROM dimension_scores WHERE run_id = ?",
                (baseline["run_id"],),
            ).fetchall()
            baseline_dim_map = {d["dimension"]: d["score"] for d in baseline_dims}
            worst = []
            for dim in result.dimensions:
                base_score = baseline_dim_map.get(dim.name, 0)
                dim_delta = dim.score - base_score
                worst.append({"dimension": dim.name, "score": dim.score, "baseline": base_score, "delta": dim_delta})
            worst.sort(key=lambda x: x["delta"])
            return {
                "severity": "critical" if delta < -0.20 else "warning",
                "overall_delta": round(delta, 3),
                "baseline_overall": baseline["overall"],
                "baseline_run_id": baseline["run_id"],
                "worst_dimensions": worst[:3],
            }
        return None

    # ─── Batches ───

    def latest_batch(self, repo: str, feature: str) -> str | None:
        row = self.conn.execute(
            "SELECT batch_id FROM eval_runs WHERE repo = ? AND feature = ? ORDER BY created_at DESC LIMIT 1",
            (repo, feature),
        ).fetchone()
        return row["batch_id"] if row else None

    def batches(self, repo: str, feature: str, limit: int = 20) -> list[dict]:
        """Batches newest first, with what they were judged with — the basis for comparing versions."""
        rows = self.conn.execute(
            """SELECT batch_id, MIN(created_at) AS created_at, COUNT(*) AS n,
                      SUM(verdict = 'PASS') AS passed, AVG(overall) AS mean,
                      GROUP_CONCAT(DISTINCT rubric_ver) AS rubric_ver,
                      GROUP_CONCAT(DISTINCT rubric_hash) AS rubric_hash,
                      GROUP_CONCAT(DISTINCT judge_prompt_hash) AS judge_prompt_hash,
                      GROUP_CONCAT(DISTINCT judge_model) AS judge_model,
                      GROUP_CONCAT(DISTINCT tool_version) AS tool_version,
                      GROUP_CONCAT(DISTINCT input_format) AS input_format
               FROM eval_runs WHERE repo = ? AND feature = ?
               GROUP BY batch_id ORDER BY MIN(created_at) DESC LIMIT ?""",
            (repo, feature, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def batch_runs(self, batch_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM eval_runs WHERE batch_id = ? ORDER BY config_name, created_at", (batch_id,)
        ).fetchall()
        runs = []
        for row in rows:
            run = dict(row)
            dims = self.conn.execute("SELECT * FROM dimension_scores WHERE run_id = ?", (run["id"],)).fetchall()
            run["dimensions"] = [dict(d) for d in dims]
            runs.append(run)
        return runs

    def batch_is_baseline(self, batch_id: str) -> bool:
        row = self.conn.execute(
            """SELECT COUNT(*) AS n FROM baselines b JOIN eval_runs r ON r.id = b.run_id
            WHERE r.batch_id = ? AND b.active = 1""",
            (batch_id,),
        ).fetchone()
        return row["n"] > 0

    # ─── Human labels (judge calibration) ───

    def save_human_label(self, run_id: str, dimension: str, score: float, labeler: str, note: str = ""):
        self.conn.execute(
            """INSERT OR REPLACE INTO human_labels (run_id, dimension, score, labeler, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, dimension, score, labeler, note, datetime.utcnow().isoformat(timespec="seconds")),
        )

    def commit(self):
        self.conn.commit()

    def human_labels(self, run_ids: list[str]) -> list[dict]:
        if not run_ids:
            return []
        rows = self.conn.execute(
            f"SELECT * FROM human_labels WHERE run_id IN ({','.join('?' * len(run_ids))})", run_ids
        ).fetchall()
        return [dict(r) for r in rows]

    def has_human_labels(self, repo: str, feature: str) -> bool:
        row = self.conn.execute(
            """SELECT COUNT(*) AS n FROM human_labels h JOIN eval_runs r ON r.id = h.run_id
            WHERE r.repo = ? AND r.feature = ?""",
            (repo, feature),
        ).fetchone()
        return row["n"] > 0

    # ─── Usage log ───

    def log_usage(self, command: str, ok: bool, duration_ms: int, tool_version: str, at: str | None = None,
                  source: str = "cli"):
        self.conn.execute(
            "INSERT INTO usage_log (at, command, ok, duration_ms, tool_version, source) VALUES (?, ?, ?, ?, ?, ?)",
            (at or datetime.utcnow().isoformat(), command, int(ok), duration_ms, tool_version, source),
        )
        self.conn.commit()

    def usage(self, since: str) -> dict[str, dict]:
        """Per command since `since`: uses, failures, last use."""
        rows = self.conn.execute(
            """SELECT command, COUNT(*) AS n, SUM(ok = 0) AS failed, MAX(at) AS last
            FROM usage_log WHERE at >= ? GROUP BY command""", (since,)).fetchall()
        return {r["command"]: dict(r) for r in rows}

    def evidence(self, since: str) -> dict[str, dict]:
        """Work the store itself records, per kind: count and last time, since `since`."""
        queries = {
            "eval_runs": "SELECT COUNT(*) AS n, MAX(created_at) AS last FROM eval_runs WHERE created_at >= ?",
            "batches": "SELECT COUNT(DISTINCT batch_id) AS n, MAX(created_at) AS last FROM eval_runs WHERE created_at >= ?",
            "computed_scores": """SELECT COUNT(*) AS n, MAX(r.created_at) AS last FROM dimension_scores d
                JOIN eval_runs r ON r.id = d.run_id WHERE d.rationale LIKE 'Counted by code%' AND r.created_at >= ?""",
            "reports": "SELECT COUNT(*) AS n, MAX(created_at) AS last FROM reports WHERE created_at >= ?",
            "reports_sent": "SELECT COUNT(*) AS n, MAX(sent_at) AS last FROM reports WHERE sent_at >= ?",
            "human_labels": """SELECT COUNT(*) AS n, MAX(created_at) AS last FROM human_labels
                WHERE created_at >= ? AND labeler NOT LIKE 'claude%'""",
        }
        return {k: dict(self.conn.execute(q, (since,)).fetchone()) for k, q in queries.items()}

    # ─── Reports (CP4) ───

    def create_report(self, repo: str, feature: str, rubric_ver: str, batch_id: str, path: str) -> str:
        report_id = uuid.uuid4().hex[:8]
        self.conn.execute(
            """INSERT INTO reports (id, created_at, repo, feature, rubric_ver, batch_id, path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_review')""",
            (report_id, datetime.utcnow().isoformat(), repo, feature, rubric_ver, batch_id, path),
        )
        self.conn.commit()
        return report_id

    def get_report(self, report_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return dict(row) if row else None

    def list_reports(self, repo: str | None = None, feature: str | None = None, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM reports WHERE 1=1"
        params: list = []
        for col, val in (("repo", repo), ("feature", feature), ("status", status)):
            if val:
                query += f" AND {col} = ?"
                params.append(val)
        rows = self.conn.execute(query + " ORDER BY created_at DESC", params).fetchall()
        return [dict(r) for r in rows]

    def approve_report(self, report_id: str, by: str, content_hash: str):
        self.conn.execute(
            "UPDATE reports SET status = 'approved', approved_by = ?, approved_at = ?, content_hash = ? WHERE id = ?",
            (by, datetime.utcnow().isoformat(timespec="seconds"), content_hash, report_id),
        )
        self.conn.commit()

    def mark_report_sent(self, report_id: str, sent_to: str):
        self.conn.execute(
            "UPDATE reports SET status = 'sent', sent_to = ?, sent_at = ? WHERE id = ?",
            (sent_to, datetime.utcnow().isoformat(timespec="seconds"), report_id),
        )
        self.conn.commit()

    def dimension_stats(
        self, repo: str, feature: str | None = None, days: int = 14
    ) -> list[dict]:
        since = (datetime.utcnow().replace(hour=0, minute=0, second=0) - __import__("datetime").timedelta(days=days)).isoformat()
        query = """
            SELECT ds.dimension, AVG(ds.score) as avg_score,
                   COUNT(*) as run_count,
                   SUM(CASE WHEN er.verdict = 'FAIL' THEN 1 ELSE 0 END) as fail_count
            FROM dimension_scores ds
            JOIN eval_runs er ON ds.run_id = er.id
            WHERE er.repo = ? AND er.created_at >= ?
        """
        params: list = [repo, since]
        if feature:
            query += " AND er.feature = ?"
            params.append(feature)
        query += " GROUP BY ds.dimension ORDER BY avg_score ASC"
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
