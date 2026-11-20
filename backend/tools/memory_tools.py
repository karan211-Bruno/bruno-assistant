"""Memory tools and CRUD helpers for Bruno AI."""

from __future__ import annotations

from typing import Any

from backend.db import db_session, new_id, now_iso
from backend.event_bus import event_bus


def list_memories(query: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """List saved memory bubbles, optionally filtered by keyword."""
    q = (query or "").strip()
    with db_session() as conn:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                """
                SELECT * FROM memories
                 WHERE content LIKE ? OR category LIKE ? OR tags LIKE ?
                 ORDER BY created_at DESC LIMIT ?
                """,
                (like, like, like, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def remember_fact(category: str, content: str, tags: str = "") -> dict[str, Any]:
    """Save a long-term memory bubble about the user."""
    cat = (category or "general").strip() or "general"
    body = (content or "").strip()
    if not body:
        return {"ok": False, "error": "기억할 내용이 비어 있습니다."}
    mem_id = new_id("mem")
    ts = now_iso()
    with db_session() as conn:
        conn.execute(
            "INSERT INTO memories (id, category, content, tags, created_at) VALUES (?, ?, ?, ?, ?)",
            (mem_id, cat, body, (tags or "").strip(), ts),
        )
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
    item = dict(row) if row else {"id": mem_id, "category": cat, "content": body, "tags": tags, "created_at": ts}
    event_bus.publish("memory_updated", {"action": "created", "memory": item})
    return {"ok": True, "memory": item}


def search_memories(query: str) -> dict[str, Any]:
    """Search saved memory bubbles by keyword."""
    items = list_memories(query=query, limit=20)
    return {"ok": True, "count": len(items), "memories": items}


def forget_memory(memory_id: str) -> dict[str, Any]:
    """Delete a saved memory bubble by ID."""
    mid = (memory_id or "").strip()
    with db_session() as conn:
        cur = conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
    deleted = cur.rowcount > 0
    if deleted:
        event_bus.publish("memory_updated", {"action": "deleted", "id": mid})
    return {"ok": deleted, "id": mid}
