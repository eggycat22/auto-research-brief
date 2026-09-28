from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import settings
from .util import dumps, loads, now_iso, task_label, today_str

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  cron TEXT NOT NULL DEFAULT '0 7 * * *',
  timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
  config_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  config_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  body_md TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  external_id TEXT,
  score REAL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  unread INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  published_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_external ON items(source, external_id);
CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(item_id) REFERENCES items(id)
);
CREATE TABLE IF NOT EXISTS job_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  log_text TEXT NOT NULL DEFAULT '',
  token_in_hit INTEGER NOT NULL DEFAULT 0,
  token_in_miss INTEGER NOT NULL DEFAULT 0,
  token_out INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  error TEXT
);
"""

DEFAULT_SOURCES = [
    ("arXiv cs.AI/LG/CL", "arxiv", {"categories": ["cs.AI", "cs.LG", "cs.CL"], "max_results": 40}),
    ("Hugging Face Daily Papers", "hf_daily", {}),
]

DEFAULT_TASKS = [
    {
        "name": "前沿日报",
        "type": "frontier_digest",
        "cron": "20 8 * * *",
        "config_json": dumps(
            {
                "explain_count": 8,
                "min_score": 7,
                "arxiv_max": 0,
                "include_arxiv": False,
                "include_hf_daily": False,
                "window_hours": 36,
            }
        ),
    },
    {
        "name": "GitHub Star 精讲",
        "type": "github_star",
        "cron": "30 8 * * *",
        "config_json": dumps({"prefer_repo": ""}),
    },
    {
        "name": "Agentic 精读",
        "type": "agentic_reading",
        "cron": "40 8 * * *",
        "config_json": dumps({"arxiv_max": 25}),
    },
]


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(settings.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, name: str, decl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "tasks", "deleted_at", "TEXT")
        _ensure_column(conn, "job_runs", "task_name", "TEXT")
        _ensure_column(conn, "job_runs", "task_type", "TEXT")
        conn.execute(
            """UPDATE job_runs SET status='failed', error=COALESCE(NULLIF(error,''), 'process-restart'),
               finished_at=? WHERE status='running'""",
            (now_iso(),),
        )
        n_sources = conn.execute("SELECT COUNT(*) AS c FROM sources").fetchone()["c"]
        if n_sources == 0:
            for name, typ, cfg in DEFAULT_SOURCES:
                conn.execute(
                    "INSERT INTO sources(name, type, enabled, config_json) VALUES (?,?,1,?)",
                    (name, typ, dumps(cfg)),
                )
        conn.execute("UPDATE tasks SET type='frontier_digest' WHERE type='daily_briefing'")
        for row in conn.execute("SELECT id, config_json FROM tasks WHERE type='frontier_digest'").fetchall():
            cfg = loads(row["config_json"] or "{}", {})
            if "include_arxiv" in cfg:
                continue
            cfg["include_arxiv"] = False
            cfg["include_hf_daily"] = False
            cfg["explain_count"] = max(int(cfg.get("explain_count") or 3), 6)
            conn.execute("UPDATE tasks SET config_json=? WHERE id=?", (dumps(cfg), row["id"]))
        existing_types = {row["type"] for row in conn.execute("SELECT type FROM tasks WHERE deleted_at IS NULL OR deleted_at=''").fetchall()}
        ts = now_iso()
        for task in DEFAULT_TASKS:
            if task["type"] in existing_types:
                continue
            conn.execute(
                """INSERT INTO tasks(name, type, enabled, cron, timezone, config_json, created_at, updated_at)
                   VALUES (?,?,1,?,?,?,?,?)""",
                (
                    task["name"],
                    task["type"],
                    task["cron"],
                    settings.tz,
                    task["config_json"],
                    ts,
                    ts,
                ),
            )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [{k: r[k] for k in r.keys()} for r in rows]


def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def all_settings() -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


_SECRET_KEYS = {"llm_api_key", "github_token", "wecom_secret", "brave_api_key", "tavily_api_key", "access_password", "access_password_hash"}


def flag(key: str, default: bool = False) -> bool:
    raw = get_setting(key, "1" if default else "0").strip().lower()
    return raw not in {"", "0", "false", "off", "no"}


def merged_secret(key: str, env_value: str, db_key: str | None = None) -> str:
    db_key = db_key or key
    db_val = get_setting(db_key, "").strip()
    env_val = (env_value or "").strip()
    if key in _SECRET_KEYS or db_key in _SECRET_KEYS:
        return env_val or db_val
    return db_val or env_val


def list_tasks(*, trashed: bool = False) -> list[dict[str, Any]]:
    with connect() as conn:
        if trashed:
            sql = "SELECT * FROM tasks WHERE deleted_at IS NOT NULL AND deleted_at!='' ORDER BY deleted_at DESC"
        else:
            sql = "SELECT * FROM tasks WHERE deleted_at IS NULL OR deleted_at='' ORDER BY id"
        return rows_to_dicts(conn.execute(sql).fetchall())


def get_task(task_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        return row_to_dict(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


def create_task(data: dict[str, Any]) -> dict[str, Any]:
    ts = now_iso()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO tasks(name, type, enabled, cron, timezone, config_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                data["name"],
                data["type"],
                1 if data.get("enabled", True) else 0,
                data.get("cron") or "0 7 * * *",
                data.get("timezone") or settings.tz,
                data.get("config_json") or "{}",
                ts,
                ts,
            ),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)).fetchone()
        rec = row_to_dict(row)
        if not rec:
            raise RuntimeError("create_task 写后读失败")
        return rec


def update_task(task_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    current = get_task(task_id)
    if not current:
        return None
    current.update({k: v for k, v in data.items() if v is not None})
    current["updated_at"] = now_iso()
    current["enabled"] = 1 if current.get("enabled") in {1, True, "1"} else 0
    with connect() as conn:
        conn.execute(
            """UPDATE tasks SET name=?, type=?, enabled=?, cron=?, timezone=?, config_json=?, updated_at=?
               WHERE id=?""",
            (
                current["name"],
                current["type"],
                current["enabled"],
                current["cron"],
                current["timezone"],
                current["config_json"],
                current["updated_at"],
                task_id,
            ),
        )
    return get_task(task_id)


def delete_task(task_id: int) -> bool:
    ts = now_iso()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET deleted_at=?, enabled=0, updated_at=? WHERE id=? AND (deleted_at IS NULL OR deleted_at='')",
            (ts, ts, task_id),
        )
        return cur.rowcount > 0


def restore_task(task_id: int) -> bool:
    ts = now_iso()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET deleted_at=NULL, updated_at=? WHERE id=? AND deleted_at IS NOT NULL AND deleted_at!=''",
            (ts, task_id),
        )
        return cur.rowcount > 0


def list_sources() -> list[dict[str, Any]]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM sources ORDER BY id").fetchall())


def update_source(source_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            return None
        enabled = row["enabled"] if "enabled" not in data else (1 if data["enabled"] else 0)
        name = data.get("name") or row["name"]
        config_json = data.get("config_json") or row["config_json"]
        conn.execute(
            "UPDATE sources SET name=?, enabled=?, config_json=? WHERE id=?",
            (name, enabled, config_json, source_id),
        )
        return row_to_dict(conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())


def find_item(source: str, external_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        return row_to_dict(
            conn.execute(
                "SELECT * FROM items WHERE source=? AND external_id=?",
                (source, external_id),
            ).fetchone()
        )


def insert_item(data: dict[str, Any]) -> dict[str, Any]:
    ts = now_iso()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO items(kind, status, title, summary, body_md, source, source_url, external_id,
               score, tags_json, metadata_json, unread, created_at, published_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["kind"],
                data.get("status") or "drafted",
                data["title"],
                data.get("summary") or "",
                data.get("body_md") or "",
                data.get("source") or "",
                data.get("source_url") or "",
                data.get("external_id"),
                data.get("score"),
                data.get("tags_json") or "[]",
                data.get("metadata_json") or "{}",
                1 if data.get("unread", True) else 0,
                ts,
                data["published_at"] if "published_at" in data else ts,
            ),
        )
        row = conn.execute("SELECT * FROM items WHERE id=?", (cur.lastrowid,)).fetchone()
        rec = row_to_dict(row)
        if not rec:
            raise RuntimeError("insert_item 写后读失败")
        return rec


def get_item(item_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        return row_to_dict(conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())


def update_item(item_id: int, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return get_item(item_id)
    sets = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE items SET {sets} WHERE id=?", [*fields.values(), item_id])
    return get_item(item_id)


def delete_item(item_id: int) -> bool:
    with connect() as conn:
        conn.execute("DELETE FROM messages WHERE item_id=?", (item_id,))
        cur = conn.execute("DELETE FROM items WHERE id=?", (item_id,))
        return cur.rowcount > 0


def find_github_star(repo_id: Any = None, full_name: str = "") -> dict[str, Any] | None:
    if repo_id:
        found = find_item("github", f"repo:{repo_id}")
        if found:
            return found
    if not full_name:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM items WHERE source='github' AND (title=? OR external_id=?) ORDER BY id DESC LIMIT 1",
            (full_name, full_name),
        ).fetchone()
        return row_to_dict(row)


def list_items(
    *,
    kind: str | None = None,
    status: str | None = None,
    source: str | None = None,
    unread: int | None = None,
    q: str | None = None,
    limit: int = 50,
    day: str | None = None,
    order: str = "created_at DESC",
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM items WHERE 1=1"
    args: list[Any] = []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if status:
        sql += " AND status=?"
        args.append(status)
    if source:
        sql += " AND source=?"
        args.append(source)
    if unread is not None:
        sql += " AND unread=?"
        args.append(unread)
    if day:
        sql += " AND (created_at LIKE ? OR published_at LIKE ?)"
        args.extend([f"{day}%", f"{day}%"])
    if q:
        sql += " AND (title LIKE ? OR summary LIKE ? OR body_md LIKE ?)"
        like = f"%{q}%"
        args.extend([like, like, like])
    allowed_order = {
        "created_at DESC": "created_at DESC, id DESC",
        "created_at ASC": "created_at ASC, id ASC",
        "id ASC": "id ASC",
        "id DESC": "id DESC",
    }
    sql += " ORDER BY " + allowed_order.get(order, "created_at DESC, id DESC") + " LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return rows_to_dicts(conn.execute(sql, args).fetchall())


def list_messages(item_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows_to_dicts(
            conn.execute(
                "SELECT * FROM messages WHERE item_id=? ORDER BY id",
                (item_id,),
            ).fetchall()
        )


def add_message(item_id: int, role: str, content: str) -> dict[str, Any]:
    ts = now_iso()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO messages(item_id, role, content, created_at) VALUES (?,?,?,?)",
            (item_id, role, content, ts),
        )
        return row_to_dict(
            conn.execute("SELECT * FROM messages WHERE id=?", (cur.lastrowid,)).fetchone()
        )  # type: ignore[return-value]


_RUN_SQL = """SELECT job_runs.*, tasks.name AS joined_name, tasks.type AS joined_type
FROM job_runs LEFT JOIN tasks ON tasks.id = job_runs.task_id"""


def _run_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if not d:
        return None
    name = (d.get("task_name") or "").strip() or (d.get("joined_name") or "").strip()
    typ = (d.get("task_type") or "").strip() or (d.get("joined_type") or "").strip()
    d["task_name"] = name or "（任务已删）"
    d["task_type"] = typ
    d["task_label"] = task_label(typ)
    return d


def create_run(task_id: int | None) -> dict[str, Any]:
    ts = now_iso()
    name, typ = "", ""
    if task_id:
        task = get_task(task_id)
        if task:
            name = task.get("name") or ""
            typ = task.get("type") or ""
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO job_runs(task_id, status, started_at, log_text, task_name, task_type)
               VALUES (?,?,?,?,?,?)""",
            (task_id, "running", ts, "", name, typ),
        )
        row = conn.execute(_RUN_SQL + " WHERE job_runs.id=?", (cur.lastrowid,)).fetchone()
        rec = _run_dict(row)
        if not rec:
            raise RuntimeError("create_run 写后读失败")
        return rec


def get_run(run_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        return _run_dict(conn.execute(_RUN_SQL + " WHERE job_runs.id=?", (run_id,)).fetchone())


def append_log(run_id: int, line: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE job_runs SET log_text = log_text || ? WHERE id=?",
            (line.rstrip() + "\n", run_id),
        )


def add_ad_hoc_spend(
    *,
    task_type: str,
    task_name: str,
    token_in_hit: int = 0,
    token_in_miss: int = 0,
    token_out: int = 0,
    cost_usd: float = 0.0,
    log_line: str = "",
) -> dict[str, Any] | None:
    if token_in_hit <= 0 and token_in_miss <= 0 and token_out <= 0 and cost_usd <= 0:
        return None
    day = today_str()
    ts = now_iso()
    note = (log_line.rstrip() + "\n") if log_line else ""
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM job_runs WHERE task_type=? AND started_at LIKE ? ORDER BY id DESC LIMIT 1",
            (task_type, f"{day}%"),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE job_runs SET token_in_hit=token_in_hit+?, token_in_miss=token_in_miss+?,
                   token_out=token_out+?, cost_usd=cost_usd+?, finished_at=?,
                   log_text=log_text || ?, status='ok' WHERE id=?""",
                (token_in_hit, token_in_miss, token_out, cost_usd, ts, note, row["id"]),
            )
            run_id = row["id"]
        else:
            cur = conn.execute(
                """INSERT INTO job_runs(task_id, status, started_at, finished_at, log_text,
                   token_in_hit, token_in_miss, token_out, cost_usd, task_name, task_type)
                   VALUES (NULL,'ok',?,?,?,?,?,?,?,?,?)""",
                (ts, ts, note, token_in_hit, token_in_miss, token_out, cost_usd, task_name, task_type),
            )
            run_id = cur.lastrowid
        return _run_dict(conn.execute(_RUN_SQL + " WHERE job_runs.id=?", (run_id,)).fetchone())


def finish_run(
    run_id: int,
    status: str,
    *,
    token_in_hit: int = 0,
    token_in_miss: int = 0,
    token_out: int = 0,
    cost_usd: float = 0.0,
    error: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE job_runs SET status=?, finished_at=?, token_in_hit=?, token_in_miss=?,
               token_out=?, cost_usd=?, error=? WHERE id=?""",
            (status, now_iso(), token_in_hit, token_in_miss, token_out, cost_usd, error, run_id),
        )


def count_runs() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM job_runs").fetchone()
        return int(row["c"] if row else 0)


def list_runs(limit: int = 30, offset: int = 0) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            _RUN_SQL + " ORDER BY job_runs.id DESC LIMIT ? OFFSET ?",
            (limit, max(offset, 0)),
        ).fetchall()
        return [d for d in (_run_dict(r) for r in rows) if d]


def list_running() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            _RUN_SQL + " WHERE job_runs.status='running' ORDER BY job_runs.id DESC"
        ).fetchall()
        return [d for d in (_run_dict(r) for r in rows) if d]


def today_cost_usd(day: str | None = None) -> float:
    day = day or today_str()
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM job_runs WHERE started_at LIKE ?",
            (f"{day}%",),
        ).fetchone()
        return float(row["s"] if row else 0)


def prompt_override(name: str) -> str:
    return get_setting(f"prompt_{name}", "")


def prompts_dir() -> Path:
    from .config import ROOT

    return ROOT / "prompts"
