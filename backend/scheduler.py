"""Background scheduler for due task reminders and active call polling."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from backend import telephony
from backend.db import db_session, get_default_conversation_id, new_id, now_iso
from backend.event_bus import event_bus

KST = ZoneInfo("Asia/Seoul")


def _is_due(due_at: str | None, current_kst: datetime) -> bool:
    if not due_at or not due_at.strip():
        return False
    raw = due_at.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt <= current_kst
    except ValueError:
        return False


def check_due_reminders() -> list[dict]:
    """Mark due un-notified tasks/reminders as notified and emit SSE + chat message."""
    now_dt = datetime.now(KST)
    fired: list[dict] = []
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'open' AND notified = 0 AND due_at IS NOT NULL AND due_at != ''"
        ).fetchall()
        for row in rows:
            item = dict(row)
            if not _is_due(item.get("due_at"), now_dt):
                continue
            conn.execute("UPDATE tasks SET notified = 1 WHERE id = ?", (item["id"],))
            item["notified"] = 1
            fired.append(item)

    if not fired:
        return []

    conv_id = get_default_conversation_id()
    ts = now_iso()
    for task in fired:
        event_bus.publish("reminder_due", {"task": task})
        event_bus.publish("task_updated", {"action": "updated", "task": task})
        msg_id = new_id("msg")
        text = f"⏰ **알림:** {task['title']}" + (f" (`{task['due_at']}`)" if task.get("due_at") else "")
        with db_session() as conn:
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, meta_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    msg_id,
                    conv_id,
                    "assistant",
                    text,
                    json.dumps({"reminder_task_id": task["id"]}, ensure_ascii=False),
                    ts,
                ),
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, conv_id))
        event_bus.publish(
            "message_created",
            {
                "conversation_id": conv_id,
                "message": {
                    "id": msg_id,
                    "conversation_id": conv_id,
                    "role": "assistant",
                    "content": text,
                    "meta": {"reminder_task_id": task["id"]},
                    "created_at": ts,
                },
            },
        )
    return fired


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    """Periodic 15-second background loop for reminders and call polling."""
    while not stop_event.is_set():
        try:
            check_due_reminders()
            await telephony.poll_active_calls()
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            continue
