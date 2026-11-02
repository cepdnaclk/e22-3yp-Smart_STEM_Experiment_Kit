"""SQLite storage for the STEM kit backend.

All data lives in the file stem_kit.db in the project folder.
Nothing is stored in the browser. The database is the only storage.
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / "stem_kit.db"

# A kit with no reading for this many seconds is "offline".
OFFLINE_AFTER_SECONDS = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS kits (
    kit_id     TEXT PRIMARY KEY,
    group_name TEXT NOT NULL DEFAULT '',
    last_seen  REAL,
    status     TEXT NOT NULL DEFAULT 'offline'
);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at   REAL
);

CREATE TABLE IF NOT EXISTS readings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_id     TEXT NOT NULL,
    session_id INTEGER,
    experiment TEXT NOT NULL,
    sensor     TEXT NOT NULL,
    value      REAL NOT NULL,
    unit       TEXT NOT NULL DEFAULT '',
    ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_kit ON readings(kit_id, id);
CREATE INDEX IF NOT EXISTS idx_readings_session ON readings(session_id);

CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_id     TEXT NOT NULL,
    session_id INTEGER,
    experiment TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at   REAL,
    notes      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kit_id       TEXT NOT NULL,
    sensor       TEXT NOT NULL,
    kind         TEXT NOT NULL,
    student_text TEXT NOT NULL DEFAULT '',
    teacher_text TEXT NOT NULL DEFAULT '',
    ts           REAL NOT NULL,
    resolved     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_kit ON alerts(kit_id, resolved);
"""


@contextmanager
def connect():
    """Open the database, save on success, roll back on error, always close."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the tables if they do not exist yet."""
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)


def now() -> float:
    return time.time()


# ---------------------------------------------------------------- sessions


def get_active_session(conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    """Return the session that is running now, or None."""
    if conn is None:
        with connect() as c:
            return get_active_session(c)
    row = conn.execute(
        "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def start_session(name: str = "") -> dict:
    """Start a new session. If one is already running, end it first."""
    name = name.strip() or time.strftime("Session %Y-%m-%d %H:%M")
    with connect() as conn:
        conn.execute("UPDATE sessions SET ended_at = ? WHERE ended_at IS NULL", (now(),))
        cur = conn.execute(
            "INSERT INTO sessions (name, started_at) VALUES (?, ?)", (name, now())
        )
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def end_session() -> Optional[dict]:
    """End the running session. Returns it, or None if nothing was running."""
    with connect() as conn:
        active = get_active_session(conn)
        if active is None:
            return None
        conn.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (now(), active["id"]))
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (active["id"],)).fetchone()
        return dict(row)


def get_sessions(limit: int = 50) -> list[dict]:
    """Newest sessions first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# -------------------------------------------------------------------- kits


def add_kit(kit_id: str, group: str) -> dict:
    """Register a kit for a group, or change the group of an existing kit."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO kits (kit_id, group_name, last_seen, status)
            VALUES (?, ?, NULL, 'offline')
            ON CONFLICT(kit_id) DO UPDATE SET group_name = excluded.group_name
            """,
            (kit_id, group),
        )
    return {"kit_id": kit_id, "group": group}


def _unresolved_count(conn: sqlite3.Connection, kit_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM alerts WHERE kit_id = ? AND resolved = 0", (kit_id,)
    ).fetchone()
    return row["n"]


def get_kits() -> list[dict]:
    """All kits with status, current experiment and connected sensors.

    Status rules:
      offline - no reading for 10 seconds
      error   - online, but there is an alert nobody has resolved
      online  - everything else
    """
    result = []
    with connect() as conn:
        kits = conn.execute("SELECT * FROM kits ORDER BY kit_id").fetchall()
        for k in kits:
            last_seen = k["last_seen"]
            if last_seen is None or now() - last_seen > OFFLINE_AFTER_SECONDS:
                status = "offline"
            elif _unresolved_count(conn, k["kit_id"]) > 0:
                status = "error"
            else:
                status = "online"
            if status != k["status"]:
                conn.execute(
                    "UPDATE kits SET status = ? WHERE kit_id = ?", (status, k["kit_id"])
                )

            latest = conn.execute(
                "SELECT experiment FROM readings WHERE kit_id = ? ORDER BY id DESC LIMIT 1",
                (k["kit_id"],),
            ).fetchone()
            sensors = conn.execute(
                """
                SELECT DISTINCT sensor FROM (
                    SELECT sensor FROM readings WHERE kit_id = ? ORDER BY id DESC LIMIT 100
                ) ORDER BY sensor
                """,
                (k["kit_id"],),
            ).fetchall()

            result.append(
                {
                    "kit_id": k["kit_id"],
                    "group": k["group_name"],
                    "status": status,
                    "last_seen": last_seen,
                    "experiment": latest["experiment"] if latest else None,
                    "sensors": [s["sensor"] for s in sensors],
                }
            )
    return result


# ---------------------------------------------------------------- readings


def insert_reading(r: dict) -> dict:
    """Save one reading, update the kit, and return the saved reading."""
    seen = now()
    ts = r.get("ts") or seen
    group = r.get("group") or ""
    with connect() as conn:
        active = get_active_session(conn)
        session_id = active["id"] if active else None

        cur = conn.execute(
            """
            INSERT INTO readings (kit_id, session_id, experiment, sensor, value, unit, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (r["kit_id"], session_id, r["experiment"], r["sensor"], r["value"], r["unit"], ts),
        )

        # last_seen uses the laptop clock, because the kit may not have a clock.
        status = "error" if _unresolved_count(conn, r["kit_id"]) > 0 else "online"
        conn.execute(
            """
            INSERT INTO kits (kit_id, group_name, last_seen, status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(kit_id) DO UPDATE SET
                group_name = CASE WHEN excluded.group_name != ''
                                  THEN excluded.group_name ELSE kits.group_name END,
                last_seen = excluded.last_seen,
                status = excluded.status
            """,
            (r["kit_id"], group, seen, status),
        )

        kit = conn.execute(
            "SELECT group_name FROM kits WHERE kit_id = ?", (r["kit_id"],)
        ).fetchone()

        return {
            "id": cur.lastrowid,
            "kit_id": r["kit_id"],
            "group": kit["group_name"],
            "session_id": session_id,
            "experiment": r["experiment"],
            "sensor": r["sensor"],
            "value": r["value"],
            "unit": r["unit"],
            "ts": ts,
        }


def get_readings(kit_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    """The newest `limit` readings, returned oldest first (good for graphs)."""
    limit = max(1, min(limit, 5000))
    with connect() as conn:
        if kit_id:
            rows = conn.execute(
                "SELECT * FROM readings WHERE kit_id = ? ORDER BY id DESC LIMIT ?",
                (kit_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM readings ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in reversed(rows)]


# -------------------------------------------------------------------- runs


def create_run(r: dict) -> dict:
    """Save one student run. It joins the running session, if there is one."""
    started = r.get("started_at") or now()
    with connect() as conn:
        active = get_active_session(conn)
        session_id = active["id"] if active else None
        cur = conn.execute(
            """
            INSERT INTO runs (kit_id, session_id, experiment, started_at, ended_at, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (r["kit_id"], session_id, r["experiment"], started, r.get("ended_at"), r.get("notes", "")),
        )
        row = conn.execute(
            """
            SELECT r.*, COALESCE(k.group_name, '') AS "group"
            FROM runs r LEFT JOIN kits k ON k.kit_id = r.kit_id
            WHERE r.id = ?
            """,
            (cur.lastrowid,),
        ).fetchone()
        return dict(row)


def get_runs(group: Optional[str] = None) -> list[dict]:
    """Saved runs, newest first. Give a group to see only that group."""
    sql = """
        SELECT r.*, COALESCE(k.group_name, '') AS "group"
        FROM runs r LEFT JOIN kits k ON k.kit_id = r.kit_id
    """
    params: tuple = ()
    if group:
        sql += " WHERE k.group_name = ?"
        params = (group,)
    sql += " ORDER BY r.id DESC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ------------------------------------------------------------------ alerts


def _alert_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["resolved"] = bool(d["resolved"])
    return d


def create_alert(kit_id: str, sensor: str, kind: str,
                 student_text: str = "", teacher_text: str = "") -> dict:
    """Save a new alert. The kit shows as 'error' until it is resolved."""
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO alerts (kit_id, sensor, kind, student_text, teacher_text, ts, resolved)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (kit_id, sensor, kind, student_text, teacher_text, now()),
        )
        row = conn.execute("SELECT * FROM alerts WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _alert_dict(row)


def get_alerts(resolved: Optional[bool] = None, limit: int = 200) -> list[dict]:
    """Newest alerts first. Use resolved=False to see only open alerts."""
    sql = "SELECT * FROM alerts"
    params: list = []
    if resolved is not None:
        sql += " WHERE resolved = ?"
        params.append(1 if resolved else 0)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        return [_alert_dict(r) for r in conn.execute(sql, params).fetchall()]


def resolve_alert(alert_id: int) -> Optional[dict]:
    """Mark one alert as resolved. Returns it, or None if it does not exist."""
    with connect() as conn:
        conn.execute("UPDATE alerts SET resolved = 1 WHERE id = ?", (alert_id,))
        row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        return _alert_dict(row) if row else None


# ------------------------------------------------------------------ export


def export_rows(session_id: Optional[int] = None) -> list[dict]:
    """Readings for the CSV file. Give a session_id, or get everything."""
    sql = """
        SELECT r.ts, r.kit_id, COALESCE(k.group_name, '') AS "group", r.experiment,
               r.sensor, r.value, r.unit, r.session_id
        FROM readings r LEFT JOIN kits k ON k.kit_id = r.kit_id
    """
    params: tuple = ()
    if session_id is not None:
        sql += " WHERE r.session_id = ?"
        params = (session_id,)
    sql += " ORDER BY r.id"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
