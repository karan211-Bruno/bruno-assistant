"""Task and reminder tools and CRUD helpers."""

from __future__ import annotations

from typing import Any

from backend.db import db_session, new_id, now_iso
from backend.event_bus import event_bus


def list_tasks(status: str = "all", limit: int = 100) -> dict[str, Any]:
    """List tasks filtered by status ('open', 'done', or 'all')."""
    st = (status or "all").strip().lower()
    with db_session() as conn:
        if st in {"open", "done"}:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY COALESCE(due_at, '9999') ASC, created_at DESC LIMIT ?",
                (st, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, COALESCE(due_at, '9999') ASC, created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    items = [dict(r) for r in rows]
    return {"ok": True, "count": len(items), "tasks": items}


def create_task(
    title: str,
    due_at: str = "",
    kind: str = "task",
    notes: str = "",
) -> dict[str, Any]:
    """Create a new task or reminder."""
    clean_title = (title or "").strip()
    if not clean_title:
        return {"ok": False, "error": "할 일 또는 알림 제목이 필요합니다."}
    tid = new_id("tsk")
    ts = now_iso()
    clean_kind = (kind or "task").strip() or "task"
    clean_due = (due_at or "").strip() or None
    with db_session() as conn:
        conn.execute(
            """
            INSERT INTO tasks (id, title, due_at, kind, status, notified, notes, created_at)
            VALUES (?, ?, ?, ?, 'open', 0, ?, ?)
            """,
            (tid, clean_title, clean_due, clean_kind, (notes or "").strip(), ts),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    task = dict(row) if row else {"id": tid, "title": clean_title, "status": "open"}
    event_bus.publish("task_updated", {"action": "created", "task": task})
    return {"ok": True, "task": task}


def update_task(task_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Update task status, title, due_at, or notes."""
    tid = (task_id or "").strip()
    with db_session() as conn:
        existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        if not existing:
            return {"ok": False, "error": "작업을 찾을 수 없습니다."}
        title = str(updates.get("title", existing["title"])).strip()
        due_at = updates.get("due_at", existing["due_at"])
        status = str(updates.get("status", existing["status"])).strip()
        if status not in {"open", "done"}:
            status = existing["status"]
        notes = str(updates.get("notes", existing["notes"] or "")).strip()
        notified = int(updates.get("notified", existing["notified"]))
        conn.execute(
            "UPDATE tasks SET title = ?, due_at = ?, status = ?, notes = ?, notified = ? WHERE id = ?",
            (title, due_at, status, notes, notified, tid),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    task = dict(row) if row else {}
    event_bus.publish("task_updated", {"action": "updated", "task": task})
    return {"ok": True, "task": task}


def complete_task(task_id: str) -> dict[str, Any]:
    """Mark a task as done."""
    return update_task(task_id, {"status": "done"})


def delete_task(task_id: str) -> dict[str, Any]:
    """Delete a task by ID."""
    tid = (task_id or "").strip()
    with db_session() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
    deleted = cur.rowcount > 0
    if deleted:
        event_bus.publish("task_updated", {"action": "deleted", "id": tid})
    return {"ok": deleted, "id": tid}
