from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  active INTEGER NOT NULL DEFAULT 1,
  stopped_at TEXT,
  stop_reason TEXT NOT NULL DEFAULT '',
  state_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS line_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  line_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  gate TEXT NOT NULL,
  chute_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(line_id) REFERENCES lines(id)
);
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_day TEXT NOT NULL,
  name TEXT NOT NULL,
  sealed INTEGER NOT NULL DEFAULT 0,
  sealed_at TEXT,
  UNIQUE(business_day, name)
);
CREATE TABLE IF NOT EXISTS chutes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  line_id INTEGER NOT NULL,
  code TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '口门开',
  soft_cap INTEGER NOT NULL DEFAULT 200,
  UNIQUE(line_id, code),
  FOREIGN KEY(line_id) REFERENCES lines(id)
);
CREATE TABLE IF NOT EXISTS route_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dest_code TEXT NOT NULL UNIQUE,
  chute_id INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  barcode TEXT NOT NULL,
  dest_code TEXT NOT NULL DEFAULT '',
  shift_id INTEGER NOT NULL,
  line_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT '待路由',
  chute_id INTEGER,
  bag_id INTEGER,
  divert_batch_id INTEGER,
  scanned_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(shift_id) REFERENCES shifts(id),
  FOREIGN KEY(line_id) REFERENCES lines(id),
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS route_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scan_id INTEGER NOT NULL,
  chute_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(scan_id) REFERENCES scans(id),
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS divert_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL,
  code TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT '回流中',
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(shift_id) REFERENCES shifts(id)
);
CREATE TABLE IF NOT EXISTS bags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_id INTEGER NOT NULL,
  chute_id INTEGER NOT NULL,
  code TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT '集包中',
  sealed_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(shift_id) REFERENCES shifts(id),
  FOREIGN KEY(chute_id) REFERENCES chutes(id)
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_line_events_line ON line_events(line_id, id);
"""

# 停机写闸口径：全局唯一，前后端同源。
GATE_POLICY = "线停即门禁落"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # 已有连接持锁时降级为默认日志模式，不影响事务语义。
        pass
    return conn


@contextmanager
def db():
    """单连接、单事务。BEGIN IMMEDIATE 让·停机·与·装载·在库内串行，
    半截写要么整体提交要么整体回滚；写闸在持锁后判定，不会被并发绕过。"""
    conn = connect()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate() -> None:
    """老库补列/补事件，保证既有分拣线有可重放的初始开机事件。"""
    with db() as conn:
        cols = _columns(conn, "lines")
        for name, ddl in (
            ("stopped_at", "ALTER TABLE lines ADD COLUMN stopped_at TEXT"),
            ("stop_reason", "ALTER TABLE lines ADD COLUMN stop_reason TEXT NOT NULL DEFAULT ''"),
            ("state_version", "ALTER TABLE lines ADD COLUMN state_version INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in cols:
                conn.execute(ddl)
        for line in conn.execute("SELECT id FROM lines").fetchall():
            n = conn.execute(
                "SELECT COUNT(*) c FROM line_events WHERE line_id=?", (line["id"],)
            ).fetchone()["c"]
            if n == 0:
                row = conn.execute("SELECT active FROM lines WHERE id=?", (line["id"],)).fetchone()
                action = "恢复" if row["active"] else "停机"
                conn.execute(
                    "INSERT INTO line_events(line_id, action, gate, chute_count, created_at, note)"
                    " VALUES (?, ?, ?, ?, datetime('now','localtime'), ?)",
                    (line["id"], action, "开" if row["active"] else "拒装禁落", 0, "迁移补初始状态"),
                )


def init_db() -> None:
    # executescript 自带提交语义，单独走裸连接，避免与外层事务冲突。
    raw = connect()
    try:
        raw.executescript(SCHEMA)
        raw.commit()
    finally:
        raw.close()
    migrate()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
