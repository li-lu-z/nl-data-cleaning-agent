"""会话持久化：用 SQLite 保存 DataFrame、对话历史与清洗步骤。"""

import json
import pickle
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "sessions.db"
_LOCK = threading.Lock()


def init_db() -> None:
    """建表（幂等）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                filename TEXT,
                df BLOB,
                original BLOB,
                history TEXT,
                steps TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_session(session_id: str, filename: str, df, original, history: list, steps: list) -> None:
    """写入或更新一个会话。"""
    with _LOCK:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                """
                INSERT INTO sessions(session_id, filename, df, original, history, steps)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET
                    filename=excluded.filename,
                    df=excluded.df,
                    original=excluded.original,
                    history=excluded.history,
                    steps=excluded.steps,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    session_id,
                    filename,
                    pickle.dumps(df),
                    pickle.dumps(original),
                    json.dumps(history, ensure_ascii=False),
                    json.dumps(steps, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def load_session(session_id: str):
    """读取会话，不存在返回 None。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return {
        "session_id": row["session_id"],
        "filename": row["filename"],
        "df": pickle.loads(row["df"]),
        "original": pickle.loads(row["original"]),
        "history": json.loads(row["history"] or "[]"),
        "steps": json.loads(row["steps"] or "[]"),
    }


def list_sessions(limit: int = 50) -> list[dict]:
    """按更新时间倒序返回会话列表。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT session_id, filename, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
