"""Outbound AI telephony engine supporting simulation, Vapi, Retell, and Twilio + OpenAI Realtime."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import httpx

from backend.config import get_settings
from backend.db import db_session, get_default_conversation_id, new_id, now_iso, row_to_dict
from backend.event_bus import event_bus

_ACTIVE_TASKS: set[asyncio.Task[Any]] = set()
ACTIVE_STATUSES = ("queued", "dialing", "ringing", "in_progress")


def normalize_kr_phone(raw: str) -> str:
    """Normalize Korean or international phone numbers into E.164 where possible."""
    text = (raw or "").strip()
    if not text:
        return ""
    has_plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return text
    if has_plus:
        return f"+{digits}"
    if digits.startswith("00"):
        return f"+{digits[2:]}"
    if digits.startswith("0") and len(digits) >= 9:
        return f"+82{digits[1:]}"
    if digits.startswith("82") and len(digits) >= 10:
        return f"+{digits}"
    return digits


def build_twilio_twiml(ws_url: str) -> str:
    """Build valid Twilio TwiML XML connecting a call to a bidirectional Media Stream."""
    safe_url = xml_escape(ws_url, {'"': "&quot;", "'": "&apos;"})
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Connect>\n"
        f'    <Stream url="{safe_url}" />\n'
        "  </Connect>\n"
        "</Response>\n"
    )


def _deserialize_call(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    try:
        item["transcript"] = json.loads(item.get("transcript_json") or "[]")
    except Exception:
        item["transcript"] = []
    return item


def get_call(call_id: str) -> dict[str, Any] | None:
    """Fetch a single call record."""
    with db_session() as conn:
        row = conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
        return _deserialize_call(row_to_dict(row))


def list_calls(limit: int = 50) -> list[dict[str, Any]]:
    """Return recent calls ordered newest first."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM calls ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_deserialize_call(dict(r)) for r in rows if r]  # type: ignore[misc]


def create_call_record(
    *,
    phone_number: str,
    goal: str,
    recipient_name: str = "",
    script: str = "",
    provider: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Create a queued call record in SQLite and publish an SSE update."""
    settings = get_settings(include_secrets=False)
    resolved_provider = (provider or settings.get("telephony_provider") or "simulation").strip()
    ts = now_iso()
    call_id = new_id("call")
    normalized = normalize_kr_phone(phone_number)
    with db_session() as conn:
        conn.execute(
            """
            INSERT INTO calls (
                id, conversation_id, recipient_name, phone_number, goal, script,
                provider, provider_call_id, status, transcript_json, summary, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, '[]', NULL, ?, ?)
            """,
            (
                call_id,
                conversation_id or get_default_conversation_id(),
                recipient_name.strip(),
                normalized or phone_number.strip(),
                goal.strip(),
                script.strip(),
                resolved_provider,
                "queued",
                ts,
                ts,
            ),
        )
    call = get_call(call_id)
    assert call is not None
    event_bus.publish("call_updated", {"call": call})
    return call


def update_call(
    call_id: str,
    *,
    status: str | None = None,
    provider_call_id: str | None = None,
    transcript: list[dict[str, Any]] | None = None,
    summary: str | None = None,
) -> dict[str, Any] | None:
    """Update fields on a call row and notify SSE clients."""
    existing = get_call(call_id)
    if not existing:
        return None
    prev_status = existing["status"]
    new_status = status if status is not None else existing["status"]
    new_provider_id = (
        provider_call_id if provider_call_id is not None else existing.get("provider_call_id")
    )
    new_transcript = transcript if transcript is not None else existing.get("transcript", [])
    new_summary = summary if summary is not None else existing.get("summary")
    ts = now_iso()

    with db_session() as conn:
        conn.execute(
            """
            UPDATE calls
               SET status = ?,
                   provider_call_id = ?,
                   transcript_json = ?,
                   summary = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (
                new_status,
                new_provider_id,
                json.dumps(new_transcript, ensure_ascii=False),
                new_summary,
                ts,
                call_id,
            ),
        )

    updated = get_call(call_id)
    if updated:
        event_bus.publish("call_updated", {"call": updated})
        if prev_status != new_status and new_status in ("completed", "failed"):
            _post_call_result_to_chat(updated)
    return updated


def append_call_transcript(call_id: str, speaker: str, text: str) -> dict[str, Any] | None:
    """Append a single transcript turn to a call record."""
    existing = get_call(call_id)
    if not existing or not text.strip():
        return existing
    transcript = list(existing.get("transcript") or [])
    transcript.append({"speaker": speaker, "text": text.strip(), "at": now_iso()})
    return update_call(call_id, transcript=transcript)


def _post_call_result_to_chat(call: dict[str, Any]) -> None:
    """Post a completion/failure summary message back into the linked chat conversation."""
    conv_id = call.get("conversation_id") or get_default_conversation_id()
    target = call.get("recipient_name") or call.get("phone_number")
    status_label = "통화 완료" if call.get("status") == "completed" else "통화 실패"
    summary = call.get("summary") or "요약 내용이 없습니다."
    prefix = "[시뮬레이션] " if call.get("provider") == "simulation" else ""
    body = f"{prefix}**[{status_label}] {target}** (`{call.get('phone_number')}`)\n- 목적: {call.get('goal')}\n- 결과: {summary}"

    ts = now_iso()
    msg_id = new_id("msg")
    with db_session() as conn:
        row = conn.execute("SELECT id FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if not row:
            return
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, meta_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                msg_id,
                conv_id,
                "assistant",
                body,
                json.dumps({"call_id": call["id"], "provider": call.get("provider")}, ensure_ascii=False),
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
                "content": body,
                "meta": {"call_id": call["id"], "provider": call.get("provider")},
                "created_at": ts,
            },
        },
    )


def _spawn_background(coro: Any) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro)
    _ACTIVE_TASKS.add(task)
    task.add_done_callback(_ACTIVE_TASKS.discard)
    return task


async def _run_simulation_call(call_id: str, user_name: str) -> None:
    """Simulate a realistic Korean outbound AI call lifecycle in the background."""
    try:
        scale = max(0.02, float(os.environ.get("BRUNO_SIM_SCALE", "1.0")))
    except ValueError:
        scale = 1.0

    call = get_call(call_id)
    if not call:
        return
    recipient = call.get("recipient_name") or call.get("phone_number") or "상대방"
    goal = call.get("goal") or "일정 확인"
    extra_script = call.get("script") or ""

    update_call(call_id, status="dialing")
    await asyncio.sleep(1.2 * scale)

    update_call(call_id, status="ringing")
    await asyncio.sleep(1.8 * scale)

    update_call(call_id, status="in_progress")
    turns = [
        ("AI 비서", f"[시뮬레이션] 안녕하세요, {user_name}님의 AI 비서입니다. {recipient} 맞으실까요?"),
        (recipient, "[시뮬레이션] 네, 안녕하세요. 무슨 일이시죠?"),
        (
            "AI 비서",
            f"[시뮬레이션] {goal} 관련해서 확인차 연락드렸습니다."
            + (f" ({extra_script})" if extra_script else ""),
        ),
        (recipient, f"[시뮬레이션] 네, 확인했습니다. '{goal}' 요청하신 대로 접수해 드릴게요."),
        ("AI 비서", "[시뮬레이션] 감사합니다. 좋은 하루 보내세요!"),
    ]
    for speaker, text in turns:
        await asyncio.sleep(0.65 * scale)
        append_call_transcript(call_id, speaker, text)

    await asyncio.sleep(0.4 * scale)
    summary = (
        f"[시뮬레이션] {recipient} 측과 통화가 완료되어 '{goal}' 건이 확인·접수되었습니다. "
        "(실제 발신은 설정에서 Vapi / Retell / Twilio 키를 입력하면 동작합니다.)"
    )
    update_call(call_id, status="completed", summary=summary)


def _korean_system_prompt(user_name: str, recipient: str, goal: str, script: str) -> str:
    return (
        f"당신은 {user_name}님의 개인 AI 비서입니다. 상대방({recipient or '수신자'})에게 정중하고 자연스러운 한국어로 통화하세요. "
        "통화 시작 시 자신이 AI 비서임을 밝히고 용건을 간단명료하게 전달하세요.\n"
        f"- 통화 목적: {goal}\n"
        + (f"- 추가 안내/스크립트: {script}\n" if script else "")
        + "- 상대방의 답변을 정확히 확인하고, 합의된 시간·조건을 복창한 뒤 정중히 통화를 마무리하세요."
    )


async def place_call(call_id: str) -> dict[str, Any]:
    """Dispatch an outbound call via the configured provider."""
    call = get_call(call_id)
    if not call:
        raise ValueError(f"Call not found: {call_id}")

    settings = get_settings(include_secrets=True)
    provider = (call.get("provider") or settings.get("telephony_provider") or "simulation").strip()
    user_name = settings.get("user_name") or "사용자"
    recipient = call.get("recipient_name") or call.get("phone_number") or "상대방"
    to_number = normalize_kr_phone(call["phone_number"])

    if provider == "simulation":
        _spawn_background(_run_simulation_call(call_id, user_name))
        return get_call(call_id) or call

    if provider == "vapi":
        api_key = settings.get("vapi_api_key", "")
        phone_number_id = settings.get("vapi_phone_number_id", "")
        if not api_key or not phone_number_id:
            return update_call(
                call_id,
                status="failed",
                summary="Vapi API 키 또는 Phone Number ID가 설정되어 있지 않습니다.",
            ) or call
        payload = {
            "phoneNumberId": phone_number_id,
            "customer": {"number": to_number, "name": recipient},
            "assistant": {
                "firstMessage": f"안녕하세요, {user_name}님의 AI 비서입니다. {call['goal']} 관련해서 연락드렸습니다.",
                "model": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "system",
                            "content": _korean_system_prompt(
                                user_name, recipient, call["goal"], call.get("script") or ""
                            ),
                        }
                    ],
                },
                "transcriber": {"provider": "deepgram", "model": "nova-2", "language": "ko"},
            },
            "metadata": {"bruno_call_id": call_id},
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    "https://api.vapi.ai/call/phone",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
            return update_call(
                call_id,
                status="dialing",
                provider_call_id=str(data.get("id") or ""),
            ) or call
        except Exception as exc:
            return update_call(call_id, status="failed", summary=f"Vapi 발신 실패: {exc}") or call

    if provider == "retell":
        api_key = settings.get("retell_api_key", "")
        from_number = settings.get("retell_from_number", "")
        agent_id = settings.get("retell_agent_id", "")
        if not api_key or not from_number or not agent_id:
            return update_call(
                call_id,
                status="failed",
                summary="Retell API 키, 발신번호 또는 Agent ID가 설정되어 있지 않습니다.",
            ) or call
        payload = {
            "from_number": normalize_kr_phone(from_number),
            "to_number": to_number,
            "override_agent_id": agent_id,
            "retell_llm_dynamic_variables": {
                "user_name": user_name,
                "recipient_name": recipient,
                "goal": call["goal"],
                "script": call.get("script") or "",
            },
            "metadata": {"bruno_call_id": call_id},
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    "https://api.retellai.com/v2/create-phone-call",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
            return update_call(
                call_id,
                status="dialing",
                provider_call_id=str(data.get("call_id") or ""),
            ) or call
        except Exception as exc:
            return update_call(call_id, status="failed", summary=f"Retell 발신 실패: {exc}") or call

    if provider == "twilio_realtime":
        sid = settings.get("twilio_account_sid", "")
        token = settings.get("twilio_auth_token", "")
        from_number = settings.get("twilio_from_number", "")
        openai_key = settings.get("openai_api_key", "")
        public_base = (settings.get("public_base_url") or "").rstrip("/")
        if not (sid and token and from_number and openai_key and public_base.startswith("https://")):
            return update_call(
                call_id,
                status="failed",
                summary=(
                    "Twilio + OpenAI Realtime 발신에는 Twilio SID/Token/발신번호, OpenAI API 키, "
                    "그리고 https:// 로 시작하는 Public Base URL(Tailscale Funnel)이 필요합니다."
                ),
            ) or call
        twiml_url = f"{public_base}/api/telephony/twilio/twiml/{call_id}"
        status_url = f"{public_base}/api/telephony/twilio/status/{call_id}"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
                    auth=(sid, token),
                    data=[
                        ("To", to_number),
                        ("From", normalize_kr_phone(from_number)),
                        ("Url", twiml_url),
                        ("StatusCallback", status_url),
                        ("StatusCallbackEvent", "initiated"),
                        ("StatusCallbackEvent", "ringing"),
                        ("StatusCallbackEvent", "answered"),
                        ("StatusCallbackEvent", "completed"),
                    ],
                )
                resp.raise_for_status()
                data = resp.json()
            return update_call(
                call_id,
                status="dialing",
                provider_call_id=str(data.get("sid") or ""),
            ) or call
        except Exception as exc:
            return update_call(call_id, status="failed", summary=f"Twilio 발신 실패: {exc}") or call

    return update_call(
        call_id,
        status="failed",
        summary=f"지원하지 않는 전화 프로바이더입니다: {provider}",
    ) or call


def _map_external_status(raw: str) -> str:
    low = (raw or "").strip().lower()
    if low in {"queued", "scheduled", "initiated"}:
        return "dialing"
    if low == "ringing":
        return "ringing"
    if low in {"in-progress", "in_progress", "ongoing", "answered", "forwarding"}:
        return "in_progress"
    if low in {"ended", "completed", "registered"}:
        return "completed"
    if low in {"failed", "error", "busy", "no-answer", "canceled", "cancelled"}:
        return "failed"
    return "in_progress"


def _parse_vapi_transcript(data: dict[str, Any]) -> list[dict[str, Any]]:
    messages = data.get("messages") or data.get("artifact", {}).get("messages") or []
    result: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        text = m.get("message") or m.get("content") or ""
        if role in {"assistant", "bot", "user"} and text:
            speaker = "AI 비서" if role in {"assistant", "bot"} else "상대방"
            result.append({"speaker": speaker, "text": str(text), "at": now_iso()})
    return result


async def poll_active_calls() -> None:
    """Sync in-flight Vapi or Retell calls so status updates even without webhooks."""
    settings = get_settings(include_secrets=True)
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT * FROM calls
             WHERE provider IN ('vapi', 'retell')
               AND status IN ('queued', 'dialing', 'ringing', 'in_progress')
               AND provider_call_id IS NOT NULL
               AND provider_call_id != ''
            """
        ).fetchall()

    if not rows:
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        for row in rows:
            call = dict(row)
            pid = call["provider_call_id"]
            if call["provider"] == "vapi" and settings.get("vapi_api_key"):
                try:
                    resp = await client.get(
                        f"https://api.vapi.ai/call/{pid}",
                        headers={"Authorization": f"Bearer {settings['vapi_api_key']}"},
                    )
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    mapped = _map_external_status(str(data.get("status") or ""))
                    transcript = _parse_vapi_transcript(data) or None
                    summary = data.get("summary") or data.get("analysis", {}).get("summary")
                    update_call(call["id"], status=mapped, transcript=transcript, summary=summary)
                except Exception:
                    continue

            elif call["provider"] == "retell" and settings.get("retell_api_key"):
                try:
                    resp = await client.get(
                        f"https://api.retellai.com/v2/get-call/{pid}",
                        headers={"Authorization": f"Bearer {settings['retell_api_key']}"},
                    )
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    mapped = _map_external_status(str(data.get("call_status") or ""))
                    turns = []
                    for item in data.get("transcript_object") or []:
                        if isinstance(item, dict) and item.get("content"):
                            speaker = "AI 비서" if item.get("role") == "agent" else "상대방"
                            turns.append({"speaker": speaker, "text": item["content"], "at": now_iso()})
                    summary = data.get("call_analysis", {}).get("call_summary")
                    update_call(
                        call["id"],
                        status=mapped,
                        transcript=turns or None,
                        summary=summary,
                    )
                except Exception:
                    continue


def handle_vapi_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Process Vapi status/end-of-call-report webhook payloads."""
    msg = payload.get("message") or payload
    call_obj = msg.get("call") or payload.get("call") or {}
    metadata = call_obj.get("metadata") or msg.get("metadata") or {}
    call_id = metadata.get("bruno_call_id")
    provider_call_id = call_obj.get("id")
    if not call_id and provider_call_id:
        with db_session() as conn:
            row = conn.execute(
                "SELECT id FROM calls WHERE provider_call_id = ?", (provider_call_id,)
            ).fetchone()
            if row:
                call_id = row["id"]
    if not call_id:
        return {"ok": True, "ignored": True}

    status_raw = str(msg.get("status") or call_obj.get("status") or "")
    msg_type = str(msg.get("type") or "")
    mapped = "completed" if msg_type == "end-of-call-report" else _map_external_status(status_raw)
    transcript = _parse_vapi_transcript(msg) or _parse_vapi_transcript(call_obj) or None
    summary = msg.get("summary") or msg.get("analysis", {}).get("summary")
    updated = update_call(str(call_id), status=mapped, transcript=transcript, summary=summary)
    return {"ok": True, "call": updated}


def handle_retell_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Process Retell webhook callbacks."""
    call_obj = payload.get("call") or payload
    metadata = call_obj.get("metadata") or {}
    call_id = metadata.get("bruno_call_id")
    pid = call_obj.get("call_id")
    if not call_id and pid:
        with db_session() as conn:
            row = conn.execute(
                "SELECT id FROM calls WHERE provider_call_id = ?", (pid,)
            ).fetchone()
            if row:
                call_id = row["id"]
    if not call_id:
        return {"ok": True, "ignored": True}

    mapped = _map_external_status(str(call_obj.get("call_status") or payload.get("event") or ""))
    turns = []
    for item in call_obj.get("transcript_object") or []:
        if isinstance(item, dict) and item.get("content"):
            speaker = "AI 비서" if item.get("role") == "agent" else "상대방"
            turns.append({"speaker": speaker, "text": item["content"], "at": now_iso()})
    summary = call_obj.get("call_analysis", {}).get("call_summary")
    updated = update_call(str(call_id), status=mapped, transcript=turns or None, summary=summary)
    return {"ok": True, "call": updated}


def handle_twilio_status(call_id: str, call_status: str) -> dict[str, Any] | None:
    """Update a call row from a Twilio StatusCallback webhook."""
    mapped = _map_external_status(call_status)
    summary = "Twilio 통화가 종료되었습니다." if mapped == "completed" else None
    return update_call(call_id, status=mapped, summary=summary)


async def handle_twilio_media_stream(websocket: Any, call_id: str) -> None:
    """Bridge Twilio Media Streams (g711_ulaw) with OpenAI Realtime API."""
    import websockets

    await websocket.accept()
    call = get_call(call_id)
    settings = get_settings(include_secrets=True)
    openai_key = settings.get("openai_api_key", "")
    if not call or not openai_key:
        await websocket.close(code=1008)
        return

    user_name = settings.get("user_name") or "사용자"
    recipient = call.get("recipient_name") or "상대방"
    instructions = _korean_system_prompt(user_name, recipient, call["goal"], call.get("script") or "")

    stream_sid: str | None = None
    update_call(call_id, status="in_progress")

    oa_url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"
    oa_headers = {
        "Authorization": f"Bearer {openai_key}",
        "OpenAI-Beta": "realtime=v1",
    }

    try:
        async with websockets.connect(oa_url, additional_headers=oa_headers) as oa_ws:
            await oa_ws.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "turn_detection": {"type": "server_vad"},
                            "input_audio_format": "g711_ulaw",
                            "output_audio_format": "g711_ulaw",
                            "voice": "alloy",
                            "instructions": instructions,
                            "modalities": ["text", "audio"],
                            "input_audio_transcription": {"model": "whisper-1"},
                        },
                    }
                )
            )
            await oa_ws.send(
                json.dumps(
                    {
                        "type": "response.create",
                        "response": {
                            "instructions": f"상대방에게 '{user_name}'님의 AI 비서라고 소개하고 '{call['goal']}' 용건을 먼저 정중히 말하세요."
                        },
                    }
                )
            )

            async def twilio_to_openai() -> None:
                nonlocal stream_sid
                while True:
                    raw = await websocket.receive_text()
                    data = json.loads(raw)
                    event = data.get("event")
                    if event == "start":
                        stream_sid = data.get("start", {}).get("streamSid")
                    elif event == "media":
                        payload = data.get("media", {}).get("payload")
                        if payload:
                            await oa_ws.send(
                                json.dumps({"type": "input_audio_buffer.append", "audio": payload})
                            )
                    elif event == "stop":
                        break

            async def openai_to_twilio() -> None:
                async for raw in oa_ws:
                    evt = json.loads(raw)
                    etype = evt.get("type")
                    if etype == "response.audio.delta" and stream_sid and evt.get("delta"):
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {"payload": evt["delta"]},
                                }
                            )
                        )
                    elif etype == "response.audio_transcript.done":
                        text = evt.get("transcript") or ""
                        if text:
                            append_call_transcript(call_id, "AI 비서", text)
                    elif etype == "conversation.item.input_audio_transcription.completed":
                        text = evt.get("transcript") or ""
                        if text:
                            append_call_transcript(call_id, recipient, text)

            t1 = asyncio.create_task(twilio_to_openai())
            t2 = asyncio.create_task(openai_to_twilio())
            done, pending = await asyncio.wait(
                {t1, t2}, return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()
            for d in done:
                _ = d.exception()
    except Exception as exc:
        append_call_transcript(call_id, "시스템", f"실시간 음성 브리지 종료: {exc}")
    finally:
        latest = get_call(call_id)
        if latest and latest["status"] in ACTIVE_STATUSES:
            turns = latest.get("transcript") or []
            summary = (
                turns[-1]["text"]
                if turns
                else "Twilio 실시간 음성 통화가 종료되었습니다."
            )
            update_call(call_id, status="completed", summary=summary)
        try:
            await websocket.close()
        except Exception:
            pass
