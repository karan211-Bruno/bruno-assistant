"""Tool schemas and central async execution dispatcher."""

from __future__ import annotations

from typing import Any

from backend.tools import (
    browser_tools,
    call_tools,
    contact_tools,
    memory_tools,
    task_tools,
    web_tools,
)

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "remember_fact",
        "description": "사용자의 취향, 자주 가는 곳, 중요한 개인 정보를 장기 메모리 버블에 저장합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "예: preference, schedule, family, health, general"},
                "content": {"type": "string", "description": "기억할 내용"},
                "tags": {"type": "string", "description": "쉼표로 구분된 태그"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "search_memories",
        "description": "저장된 장기 메모리에서 키워드로 기억을 검색합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 키워드"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "save_contact",
        "description": "이름과 전화번호를 연락처에 저장합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "이름 또는 상호명"},
                "phone": {"type": "string", "description": "전화번호 (예: 010-1234-5678, 02-123-4567)"},
                "relation": {"type": "string", "description": "관계/분류 (예: 가족, 식당, 병원)"},
                "notes": {"type": "string", "description": "메모"},
            },
            "required": ["name", "phone"],
        },
    },
    {
        "name": "find_contact",
        "description": "이름, 관계, 번호로 연락처를 조회합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_task",
        "description": "할 일 또는 지정된 시간의 알림(리마인더)을 생성합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "할 일 또는 알림 제목"},
                "due_at": {"type": "string", "description": "기한 또는 알림 시각 ISO-8601 (예: 2026-11-20T18:00:00+09:00), 없으면 빈 문자열"},
                "kind": {"type": "string", "description": "task 또는 reminder"},
                "notes": {"type": "string", "description": "상세 메모"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "list_tasks",
        "description": "등록된 할 일과 알림 목록을 조회합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "open, done, 또는 all"},
            },
        },
    },
    {
        "name": "complete_task",
        "description": "할 일을 완료 상태로 변경합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "완료할 작업 ID"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "web_search",
        "description": "인터넷에서 최신 정보, 가게 전화번호, 영업시간, 뉴스 등을 검색합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 쿼리"},
                "max_results": {"type": "integer", "description": "최대 결과 수 (기본 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_webpage",
        "description": "특정 웹페이지 URL의 본문 텍스트를 가져와 읽습니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "http 또는 https URL"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "make_phone_call",
        "description": "상대방이나 매장에 AI가 대신 전화를 걸어 예약, 문의, 확인을 처리합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "recipient_name": {"type": "string", "description": "수신자 이름 또는 매장명"},
                "phone_number": {"type": "string", "description": "전화번호 (연락처에 있으면 비워도 조회 가능)"},
                "goal": {"type": "string", "description": "통화 목적 (예: 오늘 저녁 7시 2명 예약)"},
                "script": {"type": "string", "description": "상대방에게 전달할 추가 세부 조건이나 멘트"},
            },
            "required": ["goal"],
        },
    },
    {
        "name": "browser_open",
        "description": "서버의 샌드박스 브라우저로 웹페이지를 열고 스크린샷을 갱신합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "열어볼 URL"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_click_selector",
        "description": "현재 샌드박스 브라우저 페이지에서 CSS 선택자 요소를 클릭합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 선택자"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_type",
        "description": "현재 샌드박스 브라우저 페이지의 입력창에 텍스트를 입력합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "입력 요소의 CSS 선택자 (비우면 현재 포커스 위치)"},
                "text": {"type": "string", "description": "입력할 텍스트"},
                "press_enter": {"type": "boolean", "description": "입력 후 Enter 키 전송 여부"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "request_human_takeover",
        "description": "로그인, 본인인증, 결제 등 사용자의 직접 조작이 필요할 때 브라우저 제어권을 사용자에게 요청합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "사용자 직접 조작이 필요한 이유"},
                "url": {"type": "string", "description": "관련 URL"},
            },
            "required": ["reason"],
        },
    },
]


async def execute_tool(
    name: str,
    args: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a named agent tool asynchronously and return a JSON-serializable result."""
    a = dict(args or {})
    ctx = dict(context or {})
    conv_id = ctx.get("conversation_id")

    if name == "remember_fact":
        return memory_tools.remember_fact(
            category=str(a.get("category") or "general"),
            content=str(a.get("content") or ""),
            tags=str(a.get("tags") or ""),
        )
    if name == "search_memories":
        return memory_tools.search_memories(query=str(a.get("query") or ""))
    if name == "save_contact":
        return contact_tools.save_contact(
            name=str(a.get("name") or ""),
            phone=str(a.get("phone") or ""),
            relation=str(a.get("relation") or ""),
            notes=str(a.get("notes") or ""),
        )
    if name == "find_contact":
        return contact_tools.find_contact(query=str(a.get("query") or ""))
    if name == "create_task":
        return task_tools.create_task(
            title=str(a.get("title") or ""),
            due_at=str(a.get("due_at") or ""),
            kind=str(a.get("kind") or "task"),
            notes=str(a.get("notes") or ""),
        )
    if name == "list_tasks":
        return task_tools.list_tasks(status=str(a.get("status") or "open"))
    if name == "complete_task":
        return task_tools.complete_task(task_id=str(a.get("task_id") or ""))
    if name == "web_search":
        return await web_tools.web_search(
            query=str(a.get("query") or ""),
            max_results=int(a.get("max_results") or 5),
        )
    if name == "fetch_webpage":
        return await web_tools.fetch_webpage(url=str(a.get("url") or ""))
    if name == "make_phone_call":
        return await call_tools.make_phone_call(
            recipient_name=str(a.get("recipient_name") or ""),
            phone_number=str(a.get("phone_number") or ""),
            goal=str(a.get("goal") or ""),
            script=str(a.get("script") or ""),
            conversation_id=conv_id,
        )
    if name == "browser_open":
        return await browser_tools.browser_manager.open_url(str(a.get("url") or ""))
    if name == "browser_click_selector":
        return await browser_tools.browser_manager.click_selector(str(a.get("selector") or ""))
    if name == "browser_type":
        return await browser_tools.browser_manager.type_text(
            selector=str(a.get("selector") or ""),
            text=str(a.get("text") or ""),
            press_enter=bool(a.get("press_enter", False)),
        )
    if name == "request_human_takeover":
        return await browser_tools.request_human_takeover(
            reason=str(a.get("reason") or "사용자 확인이 필요합니다."),
            url=str(a.get("url") or ""),
            conversation_id=conv_id,
        )

    return {"ok": False, "error": f"알 수 없는 도구입니다: {name}"}
