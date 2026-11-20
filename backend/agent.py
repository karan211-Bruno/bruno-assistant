"""ReAct agent orchestrator combining long-term memory, tools, and real-time SSE."""

from __future__ import annotations

import json
from typing import Any

from backend.config import get_settings
from backend.db import db_session, new_id, now_iso
from backend.event_bus import event_bus
from backend.llm import chat_with_tools
from backend.tools import TOOL_SPECS, execute_tool
from backend.tools.contact_tools import list_contacts
from backend.tools.memory_tools import list_memories
from backend.tools.task_tools import list_tasks

TOOL_STATUS_LABELS: dict[str, str] = {
    "remember_fact": "기억 버블 저장 중...",
    "search_memories": "기억 검색 중...",
    "save_contact": "연락처 저장 중...",
    "find_contact": "연락처 조회 중...",
    "create_task": "일정/알림 등록 중...",
    "list_tasks": "할 일 목록 확인 중...",
    "complete_task": "할 일 완료 처리 중...",
    "web_search": "웹 검색 중...",
    "fetch_webpage": "웹페이지 읽는 중...",
    "make_phone_call": "전화 발신 준비 중...",
    "browser_open": "브라우저 열기 중...",
    "browser_click_selector": "브라우저 요소 클릭 중...",
    "browser_type": "브라우저 입력 중...",
    "request_human_takeover": "사용자 직접 승인 요청 중...",
}


def _build_system_prompt(settings: dict[str, Any]) -> str:
    user_name = settings.get("user_name") or "사용자"
    tz_name = settings.get("user_timezone") or "Asia/Seoul"
    current_time = now_iso(tz_name)

    memories = list_memories(limit=12)
    contacts = list_contacts(limit=15)
    open_tasks = list_tasks(status="open", limit=15).get("tasks") or []

    mem_block = (
        "\n".join(f"- [{m['category']}] {m['content']}" for m in memories)
        or "(저장된 기억 없음)"
    )
    ct_block = (
        "\n".join(f"- {c['name']} ({c.get('relation') or '기타'}): {c['phone']}" for c in contacts)
        or "(등록된 연락처 없음)"
    )
    task_block = (
        "\n".join(
            f"- [{t['id']}] {t['title']}" + (f" (기한: {t['due_at']})" if t.get("due_at") else "")
            for t in open_tasks
        )
        or "(열린 할 일 없음)"
    )

    return (
        f"당신은 {user_name}님의 개인 AI 비서 'Bruno AI'입니다. 한국어로 명확하고 친절하며 간결하게 답하세요.\n"
        f"현재 시각 ({tz_name}): {current_time}\n\n"
        "원칙:\n"
        "1. 전화를 걸어야 할 때는 `make_phone_call` 도구를 사용하세요. 전화번호가 없고 이름만 있다면 먼저 `find_contact`나 `web_search`로 번호를 찾으세요.\n"
        "2. 로그인, 본인인증, 결제가 필요한 웹 작업에서는 절대 추측하지 말고 `request_human_takeover`로 사용자에게 브라우저 조작을 요청하세요.\n"
        "3. 사용자가 취향/정보를 말하면 `remember_fact`로 기억해 두세요.\n"
        "4. 존재하지 않는 실행 결과나 통화 결과를 지어내지 마세요.\n\n"
        f"[최근 기억 버블]\n{mem_block}\n\n"
        f"[등록된 연락처]\n{ct_block}\n\n"
        f"[진행 중인 할 일/알림]\n{task_block}\n"
    )


def _save_message(
    conversation_id: str,
    role: str,
    content: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    msg_id = new_id("msg")
    ts = now_iso()
    meta_json = json.dumps(meta or {}, ensure_ascii=False)
    with db_session() as conn:
        conv = conn.execute("SELECT id FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        if not conv:
            conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation_id, "새 대화", ts, ts),
            )
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, meta_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, conversation_id, role, content, meta_json, ts),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (ts, conversation_id),
        )
    msg_obj = {
        "id": msg_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "meta": meta or {},
        "created_at": ts,
    }
    event_bus.publish("message_created", {"conversation_id": conversation_id, "message": msg_obj})
    return msg_obj


def _load_recent_history(conversation_id: str, limit: int = 16) -> list[dict[str, Any]]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM messages
             WHERE conversation_id = ? AND role IN ('user', 'assistant')
             ORDER BY created_at DESC LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()
    history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    return history


async def run_agent_turn(conversation_id: str, user_text: str) -> dict[str, Any]:
    """Persist user message, run tool-calling loop, and persist assistant reply."""
    clean_text = (user_text or "").strip()
    if not clean_text:
        raise ValueError("메시지가 비어 있습니다.")

    user_msg = _save_message(conversation_id, "user", clean_text)
    settings = get_settings(include_secrets=True)
    system_prompt = _build_system_prompt(settings)
    working_messages = _load_recent_history(conversation_id, limit=16)

    tool_trace: list[dict[str, Any]] = []
    final_text = ""
    provider_used = "demo"

    event_bus.publish("agent_status", {"conversation_id": conversation_id, "status": "생각하는 중..."})

    try:
        for _ in range(6):
            llm_res = await chat_with_tools(
                messages=working_messages,
                system_prompt=system_prompt,
                tools=TOOL_SPECS,
                settings=settings,
            )
            provider_used = llm_res.provider_used

            if not llm_res.tool_calls:
                final_text = llm_res.text.strip() or "요청을 처리했습니다."
                break

            working_messages.append(
                {
                    "role": "assistant",
                    "content": llm_res.text or "",
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in llm_res.tool_calls
                    ],
                }
            )

            for tc in llm_res.tool_calls:
                label = TOOL_STATUS_LABELS.get(tc.name, f"{tc.name} 실행 중...")
                event_bus.publish(
                    "agent_status",
                    {"conversation_id": conversation_id, "status": label, "tool": tc.name},
                )
                result = await execute_tool(
                    tc.name,
                    tc.arguments,
                    context={"conversation_id": conversation_id},
                )
                tool_trace.append({"tool": tc.name, "args": tc.arguments, "result": result})
                working_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        else:
            final_text = "여러 도구를 연속 실행한 뒤 작업을 마쳤습니다."
    finally:
        event_bus.publish("agent_status", {"conversation_id": conversation_id, "status": ""})

    assistant_msg = _save_message(
        conversation_id,
        "assistant",
        final_text,
        meta={"provider": provider_used, "tool_trace": tool_trace},
    )
    return {
        "user_message": user_msg,
        "assistant_message": assistant_msg,
        "tool_trace": tool_trace,
        "provider": provider_used,
    }
