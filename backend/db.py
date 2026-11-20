"""SQLite persistence layer for Bruno AI."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")


def now_iso(tz_name: str = "Asia/Seoul") -> str:
    """Return current ISO-8601 timestamp in the given timezone."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = KST
    return datetime.now(tz).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    """Generate a compact unique identifier."""
    token = uuid.uuid4().hex[:12]
    return f"{prefix}_{token}" if prefix else token


def get_db_path() -> Path:
    """Resolve the active SQLite database path."""
    override = os.environ.get("BRUNO_DB_PATH", "").strip()
    path = Path(override).expanduser() if override else (PROJECT_ROOT / "data" / "bruno.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_data_dir() -> Path:
    """Return the data directory alongside the active SQLite database."""
    data_dir = get_db_path().parent
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_conn() -> sqlite3.Connection:
    """Open a configured SQLite connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(str(get_db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    """Context manager that commits on success, rolls back on error, and closes."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert an sqlite3.Row to a plain dictionary."""
    return dict(row) if row is not None else None


def init_db() -> None:
    """Create database schema idempotently and seed initial conversation."""
    with db_session() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                meta_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_messages_conv_time
                ON messages(conversation_id, created_at);

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS contacts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                relation TEXT,
                phone TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                due_at TEXT,
                kind TEXT NOT NULL DEFAULT 'task',
                status TEXT NOT NULL DEFAULT 'open',
                notified INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status_due
                ON tasks(status, due_at);

            CREATE TABLE IF NOT EXISTS calls (
                id TEXT PRIMARY KEY,
                conversation_id TEXT,
                recipient_name TEXT,
                phone_number TEXT NOT NULL,
                goal TEXT NOT NULL,
                script TEXT,
                provider TEXT NOT NULL,
                provider_call_id TEXT,
                status TEXT NOT NULL,
                transcript_json TEXT NOT NULL DEFAULT '[]',
                summary TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                conversation_id TEXT,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                decision_note TEXT,
                created_at TEXT NOT NULL,
                decided_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_approvals_status_time
                ON approvals(status, created_at);
            """
        )

        row = conn.execute("SELECT COUNT(*) AS cnt FROM conversations").fetchone()
        if row and row["cnt"] == 0:
            ts = now_iso()
            conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("default", "메인 비서", ts, ts),
            )
            welcome = (
                "안녕하세요! **Bruno AI** 개인 비서입니다.\n\n"
                "폰·태블릿·PC 어디서나 대화가 동기화되며, 아래 일을 도와드릴 수 있어요:\n"
                "- **전화 대행:** 예) \"단골 식당에 오늘 저녁 7시 2명 예약 전화해줘\"\n"
                "- **기억 & 연락처:** 예) \"엄마 번호 010-1234-5678 연락처에 저장해줘\"\n"
                "- **일정·할 일 알림:** 예) \"오늘 오후 6시 약 먹기 알림 등록해줘\"\n"
                "- **웹 검색 & 브라우저:** 예) \"강남역 근처 조용한 카페 찾아줘\"\n\n"
                "현재 기본 **데모/로컬 모드**로도 바로 테스트 가능하며, 우측 상단 **설정**에서 Gemini / OpenAI / Claude API 키와 전화 연동(Vapi · Retell · Twilio)을 입력하면 즉시 실전 모드로 전환됩니다."
            )
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, meta_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    new_id("msg"),
                    "default",
                    "assistant",
                    welcome,
                    json.dumps({"provider": "system"}, ensure_ascii=False),
                    ts,
                ),
            )


def get_default_conversation_id() -> str:
    """Return the most recently updated conversation id, or 'default'."""
    with db_session() as conn:
        row = conn.execute(
            "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return str(row["id"])
        ts = now_iso()
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("default", "메인 비서", ts, ts),
        )
        return "default"
