"""Contact directory tools and CRUD helpers."""

from __future__ import annotations

from typing import Any

from backend.db import db_session, new_id, now_iso
from backend.event_bus import event_bus


def list_contacts(query: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """List contacts, optionally filtered by name, relation, or phone."""
    q = (query or "").strip()
    with db_session() as conn:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                """
                SELECT * FROM contacts
                 WHERE name LIKE ? OR relation LIKE ? OR phone LIKE ? OR notes LIKE ?
                 ORDER BY name ASC LIMIT ?
                """,
                (like, like, like, like, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM contacts ORDER BY name ASC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def save_contact(name: str, phone: str, relation: str = "", notes: str = "") -> dict[str, Any]:
    """Create or update a contact in SQLite."""
    clean_name = (name or "").strip()
    clean_phone = (phone or "").strip()
    if not clean_name or not clean_phone:
        return {"ok": False, "error": "이름과 전화번호가 모두 필요합니다."}
    ts = now_iso()
    with db_session() as conn:
        existing = conn.execute(
            "SELECT * FROM contacts WHERE name = ? LIMIT 1", (clean_name,)
        ).fetchone()
        if existing:
            cid = existing["id"]
            conn.execute(
                "UPDATE contacts SET phone = ?, relation = ?, notes = ? WHERE id = ?",
                (
                    clean_phone,
                    (relation or existing["relation"] or "").strip(),
                    (notes or existing["notes"] or "").strip(),
                    cid,
                ),
            )
        else:
            cid = new_id("ct")
            conn.execute(
                "INSERT INTO contacts (id, name, relation, phone, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (cid, clean_name, (relation or "").strip(), clean_phone, (notes or "").strip(), ts),
            )
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (cid,)).fetchone()
    contact = dict(row) if row else {"id": cid, "name": clean_name, "phone": clean_phone}
    event_bus.publish("contact_updated", {"action": "saved", "contact": contact})
    return {"ok": True, "contact": contact}


def find_contact(query: str) -> dict[str, Any]:
    """Search contacts by name, relation, or phone."""
    items = list_contacts(query=query, limit=10)
    return {"ok": True, "count": len(items), "contacts": items}


def delete_contact(contact_id: str) -> dict[str, Any]:
    """Delete a contact by ID."""
    cid = (contact_id or "").strip()
    with db_session() as conn:
        cur = conn.execute("DELETE FROM contacts WHERE id = ?", (cid,))
    deleted = cur.rowcount > 0
    if deleted:
        event_bus.publish("contact_updated", {"action": "deleted", "id": cid})
    return {"ok": deleted, "id": cid}
