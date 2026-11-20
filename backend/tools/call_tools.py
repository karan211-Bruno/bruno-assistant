"""Outbound AI phone call tool with human approval safety gate."""

from __future__ import annotations

import json
from typing import Any

from backend import telephony
from backend.config import get_settings
from backend.db import db_session, get_default_conversation_id, new_id, now_iso
from backend.event_bus import event_bus
from backend.tools.contact_tools import list_contacts


async def make_phone_call(
    *,
    recipient_name: str = "",
    phone_number: str = "",
    goal: str = "",
    script: str = "",
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Schedule an outbound AI phone call (or create a Human-in-the-loop approval card)."""
    clean_name = (recipient_name or "").strip()
    clean_phone = (phone_number or "").strip()
    clean_goal = (goal or "").strip() or "용건 확인"
    clean_script = (script or "").strip()
    conv_id = conversation_id or get_default_conversation_id()

    if not clean_phone and clean_name:
        matches = list_contacts(query=clean_name, limit=3)
        if matches:
            clean_phone = matches[0]["phone"]
            if not clean_name:
                clean_name = matches[0]["name"]

    if not clean_phone:
        return {
            "ok": False,
            "error": (
                f"'{clean_name or '수신자'}'의 전화번호를 찾지 못했습니다. "
                "전화번호를 직접 알려주시거나 연락처에 먼저 등록해 주세요."
            ),
        }

    settings = get_settings(include_secrets=False)
    provider = str(settings.get("telephony_provider") or "simulation")
    require_approval = bool(settings.get("require_call_approval", True))
    normalized = telephony.normalize_kr_phone(clean_phone)

    if require_approval:
        approval_id = new_id("apr")
        ts = now_iso()
        target_label = clean_name or clean_phone
        title = f"AI 전화 발신 승인: {target_label}"
        description = f"번호: {clean_phone} ({normalized}) · 목적: {clean_goal}"
        payload = {
            "recipient_name": clean_name,
            "phone_number": clean_phone,
            "normalized_phone": normalized,
            "goal": clean_goal,
            "script": clean_script,
            "provider": provider,
        }
        with db_session() as conn:
            conn.execute(
                """
                INSERT INTO approvals (id, conversation_id, kind, title, description, payload_json, status, created_at)
                VALUES (?, ?, 'phone_call', ?, ?, ?, 'pending', ?)
                """,
                (
                    approval_id,
                    conv_id,
                    title,
                    description,
                    json.dumps(payload, ensure_ascii=False),
                    ts,
                ),
            )
            row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        approval = dict(row) if row else {"id": approval_id, "payload": payload}
        approval["payload"] = payload
        event_bus.publish("approval_created", {"approval": approval})
        return {
            "ok": True,
            "status": "awaiting_approval",
            "approval_id": approval_id,
            "recipient_name": clean_name,
            "phone_number": clean_phone,
            "provider": provider,
            "goal": clean_goal,
            "message": "전화 발신 승인 카드가 생성되었습니다. 사용자가 승인 버튼을 누르면 즉시 전화를 겁니다.",
        }

    call = telephony.create_call_record(
        phone_number=clean_phone,
        goal=clean_goal,
        recipient_name=clean_name,
        script=clean_script,
        provider=provider,
        conversation_id=conv_id,
    )
    dispatched = await telephony.place_call(call["id"])
    return {
        "ok": True,
        "status": dispatched.get("status", "dialing"),
        "call_id": call["id"],
        "recipient_name": clean_name,
        "phone_number": clean_phone,
        "provider": provider,
    }
