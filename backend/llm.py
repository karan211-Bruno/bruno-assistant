"""Unified LLM client supporting Gemini, OpenAI, Anthropic, Ollama, and deterministic Korean Demo mode."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

KST = ZoneInfo("Asia/Seoul")


@dataclass
class ToolCall:
    """Normalized tool call returned by any LLM backend."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Normalized LLM turn response."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider_used: str = "demo"
    error: str | None = None


def resolve_active_provider(settings: dict[str, Any]) -> str:
    """Resolve effective LLM provider from settings."""
    configured = (settings.get("llm_provider") or "auto").strip().lower()
    if configured != "auto":
        return configured
    if (settings.get("gemini_api_key") or "").strip():
        return "gemini"
    if (settings.get("openai_api_key") or "").strip():
        return "openai"
    if (settings.get("anthropic_api_key") or "").strip():
        return "anthropic"
    return "demo"


def _extract_phone(text: str) -> str:
    match = re.search(r"(\+?\d{2,3}[-\s]?\d{3,4}[-\s]?\d{4}|0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4})", text)
    return match.group(1).strip() if match else ""


def _parse_korean_due(text: str) -> str:
    now = datetime.now(KST)
    rel_min = re.search(r"(\d+)\s*분\s*(?:뒤|후)", text)
    if rel_min:
        dt = now + timedelta(minutes=int(rel_min.group(1)))
        return dt.isoformat(timespec="seconds")

    rel_hr = re.search(r"(\d+)\s*시간\s*(?:뒤|후)", text)
    if rel_hr:
        dt = now + timedelta(hours=int(rel_hr.group(1)))
        return dt.isoformat(timespec="seconds")

    ampm_match = re.search(r"(오전|오후)?\s*(\d{1,2})시(?:\s*(\d{1,2})분)?", text)
    if ampm_match:
        ampm = ampm_match.group(1) or ""
        hour = int(ampm_match.group(2))
        minute = int(ampm_match.group(3) or 0)
        if ampm == "오후" and hour < 12:
            hour += 12
        elif ampm == "오전" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if "내일" in text:
                target += timedelta(days=1)
            elif target < now:
                target += timedelta(days=1)
            return target.isoformat(timespec="seconds")

    hhmm = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if hhmm:
        hour, minute = int(hhmm.group(1)), int(hhmm.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if "내일" in text or target < now:
                target += timedelta(days=1)
            return target.isoformat(timespec="seconds")
    return ""


def _guess_recipient(text: str, phone: str) -> str:
    cleaned = text.replace(phone, " ").strip()
    m = re.search(r"([가-힣A-Za-z0-9_]{1,20}?)(?:에게|한테|에|으로|로)\s", cleaned)
    if m:
        candidate = m.group(1).strip()
        if candidate not in {"전화", "통화", "예약", "내일", "오늘", "지금", "나"}:
            return candidate
    return ""


def _demo_summarize_tool_results(tool_msgs: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for m in tool_msgs:
        name = m.get("name", "")
        try:
            res = json.loads(m.get("content") or "{}")
        except Exception:
            res = {}

        if not res.get("ok", True) and res.get("error"):
            lines.append(f"⚠️ {res['error']}")
            continue

        if name == "make_phone_call":
            if res.get("status") == "awaiting_approval":
                target = res.get("recipient_name") or res.get("phone_number")
                lines.append(
                    f"📞 **{target}** (`{res.get('phone_number')}`) 번호로 AI 전화 발신 승인 카드를 생성했습니다.\n"
                    f"- 통화 목적: {res.get('goal')}\n"
                    f"- 모드: `{res.get('provider')}`\n"
                    "아래 카드에서 **승인 후 전화 걸기**를 눌러주시면 바로 발신합니다."
                )
            else:
                lines.append(f"📞 전화를 발신했습니다 (상태: `{res.get('status')}`, ID: `{res.get('call_id')}`).")
        elif name == "remember_fact":
            mem = res.get("memory", {})
            lines.append(f"🧠 기억 버블에 저장했습니다: \"{mem.get('content', '')}\"")
        elif name == "save_contact":
            ct = res.get("contact", {})
            lines.append(f"📒 연락처에 저장했습니다: **{ct.get('name')}** (`{ct.get('phone')}`)")
        elif name == "create_task":
            tsk = res.get("task", {})
            due = f" (예정: `{tsk.get('due_at')}`)" if tsk.get("due_at") else ""
            lines.append(f"✅ 등록했습니다: **{tsk.get('title')}**{due}")
        elif name == "web_search":
            items = res.get("results") or []
            if not items:
                lines.append("🔍 검색 결과를 찾지 못했습니다.")
            else:
                lines.append(f"🔍 **'{res.get('query')}'** 검색 결과:")
                for item in items[:4]:
                    lines.append(f"- [{item.get('title')}]({item.get('url')}) — {item.get('snippet', '')}")
        elif name == "browser_open":
            lines.append(f"🌐 브라우저에서 열었습니다: {res.get('title') or res.get('url')}")
        else:
            lines.append(f"작업을 완료했습니다: `{json.dumps(res, ensure_ascii=False)}`")

    return "\n".join(lines) or "요청하신 작업을 처리했습니다."


def _run_demo_mode(messages: list[dict[str, Any]]) -> LLMResponse:
    if messages and messages[-1].get("role") == "tool":
        recent_tools: list[dict[str, Any]] = []
        for m in reversed(messages):
            if m.get("role") == "tool":
                recent_tools.append(m)
            else:
                break
        recent_tools.reverse()
        return LLMResponse(text=_demo_summarize_tool_results(recent_tools), provider_used="demo")

    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_text = str(m.get("content") or "").strip()
            break

    low = user_text.lower()
    phone = _extract_phone(user_text)

    if phone and any(k in user_text for k in ("연락처", "번호 저장", "저장해")):
        name_part = user_text.replace(phone, " ")
        for kw in ("연락처에", "연락처", "번호", "전화번호", "저장해줘", "저장해", "등록해줘", "등록해"):
            name_part = name_part.replace(kw, " ")
        name = re.sub(r"\s+", " ", name_part).strip(" ,.-:") or "새 연락처"
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id=f"demo_{uuid.uuid4().hex[:8]}",
                    name="save_contact",
                    arguments={"name": name, "phone": phone},
                )
            ],
            provider_used="demo",
        )

    if any(k in user_text for k in ("전화", "통화", "콜")):
        recipient = _guess_recipient(user_text, phone)
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id=f"demo_{uuid.uuid4().hex[:8]}",
                    name="make_phone_call",
                    arguments={
                        "recipient_name": recipient,
                        "phone_number": phone,
                        "goal": user_text,
                    },
                )
            ],
            provider_used="demo",
        )

    if any(k in user_text for k in ("기억해", "기억 해", "메모해", "메모 해", "잊지마")):
        fact = user_text
        for kw in ("기억해줘", "기억해 줘", "기억해", "메모해줘", "메모해", "잊지마"):
            fact = fact.replace(kw, "")
        fact = fact.strip(" .,") or user_text
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id=f"demo_{uuid.uuid4().hex[:8]}",
                    name="remember_fact",
                    arguments={"category": "general", "content": fact},
                )
            ],
            provider_used="demo",
        )

    if any(k in user_text for k in ("알림", "리마인더", "할 일", "할일", "일정")):
        due = _parse_korean_due(user_text)
        kind = "reminder" if ("알림" in user_text or "리마인더" in user_text or due) else "task"
        title = user_text
        for kw in ("알림 등록해줘", "알림 설정해줘", "알림 해줘", "할 일 추가해줘", "등록해줘", "추가해줘"):
            title = title.replace(kw, "")
        title = title.strip(" .,") or user_text
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id=f"demo_{uuid.uuid4().hex[:8]}",
                    name="create_task",
                    arguments={"title": title, "due_at": due, "kind": kind},
                )
            ],
            provider_used="demo",
        )

    url_match = re.search(r"https?://\S+", user_text)
    if url_match and any(k in user_text for k in ("열어", "들어가", "브라우저", "접속")):
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id=f"demo_{uuid.uuid4().hex[:8]}",
                    name="browser_open",
                    arguments={"url": url_match.group(0)},
                )
            ],
            provider_used="demo",
        )

    if any(k in user_text for k in ("검색", "찾아", "알아봐")) or "search" in low:
        q = user_text
        for kw in ("검색해줘", "검색해", "찾아줘", "알아봐줘", "웹에서"):
            q = q.replace(kw, "")
        q = q.strip(" .,") or user_text
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id=f"demo_{uuid.uuid4().hex[:8]}",
                    name="web_search",
                    arguments={"query": q, "max_results": 5},
                )
            ],
            provider_used="demo",
        )

    return LLMResponse(
        text=(
            f"현재 **로컬 데모 모드**로 동작 중입니다. 방금 말씀하신 내용(`{user_text}`)도 바로 다룰 수 있어요!\n\n"
            "지금 바로 실행 가능한 명령 예시:\n"
            "- **연락처:** `홍길동 010-1234-5678 연락처에 저장해줘`\n"
            "- **AI 전화:** `홍길동에게 내일 오후 3시 회의 확인 전화해줘` (또는 `02-123-4567로 저녁 7시 2명 예약 전화해줘`)\n"
            "- **기억 버블:** `나는 매운 음식을 잘 못 먹는다고 기억해줘`\n"
            "- **일정·알림:** `1분 뒤 스트레칭 하기 알림 등록해줘`\n"
            "- **웹 검색:** `강남역 파스타 맛집 검색해줘`\n\n"
            "더 자유로운 자연어 대화·복합 추론을 원하시면 우측 상단 **설정**에서 Gemini / OpenAI / Anthropic API 키를 입력해 주세요."
        ),
        provider_used="demo",
    )


def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in tools
    ]


def _to_openai_messages(messages: list[dict[str, Any]], system_prompt: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in messages:
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "content": str(m.get("content") or "")})
        elif role == "assistant":
            item: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
            tcs = m.get("tool_calls") or []
            if tcs:
                if not item["content"]:
                    item["content"] = None
                item["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments") or {}, ensure_ascii=False),
                        },
                    }
                    for tc in tcs
                ]
            out.append(item)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id") or "",
                    "content": str(m.get("content") or "{}"),
                }
            )
    return out


async def _call_openai_compatible(
    *,
    base_url: str,
    api_key: str,
    model: str,
    provider_label: str,
    messages: list[dict[str, Any]],
    system_prompt: str,
    tools: list[dict[str, Any]],
) -> LLMResponse:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: dict[str, Any] = {
        "model": model,
        "messages": _to_openai_messages(messages, system_prompt),
        "tools": _to_openai_tools(tools),
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return LLMResponse(
            text=f"{provider_label} API 호출 중 오류가 발생했습니다: {exc}",
            provider_used=provider_label,
            error=str(exc),
        )

    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    parsed_calls: list[ToolCall] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except Exception:
            args = {}
        parsed_calls.append(
            ToolCall(
                id=str(tc.get("id") or f"tc_{uuid.uuid4().hex[:8]}"),
                name=str(fn.get("name") or ""),
                arguments=args,
            )
        )
    return LLMResponse(text=text, tool_calls=parsed_calls, provider_used=provider_label)


async def _call_gemini(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    system_prompt: str,
    tools: list[dict[str, Any]],
) -> LLMResponse:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    contents: list[dict[str, Any]] = []
    pending_tool_parts: list[dict[str, Any]] = []

    def flush_tools() -> None:
        nonlocal pending_tool_parts
        if pending_tool_parts:
            contents.append({"role": "user", "parts": pending_tool_parts})
            pending_tool_parts = []

    for m in messages:
        role = m.get("role")
        if role == "tool":
            try:
                res_obj = json.loads(m.get("content") or "{}")
                if not isinstance(res_obj, dict):
                    res_obj = {"result": res_obj}
            except Exception:
                res_obj = {"result": str(m.get("content") or "")}
            pending_tool_parts.append(
                {
                    "functionResponse": {
                        "name": m.get("name") or "tool",
                        "response": res_obj,
                    }
                }
            )
            continue

        flush_tools()
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": str(m.get("content") or "")}]})
        elif role == "assistant":
            parts: list[dict[str, Any]] = []
            if m.get("content"):
                parts.append({"text": str(m["content"])})
            for tc in m.get("tool_calls") or []:
                parts.append(
                    {
                        "functionCall": {
                            "name": tc["name"],
                            "args": tc.get("arguments") or {},
                        }
                    }
                )
            if parts:
                contents.append({"role": "model", "parts": parts})
    flush_tools()

    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "tools": [
            {
                "functionDeclarations": [
                    {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"],
                    }
                    for t in tools
                ]
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, params={"key": api_key}, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return LLMResponse(
            text=f"Gemini API 호출 중 오류가 발생했습니다: {exc}",
            provider_used="gemini",
            error=str(exc),
        )

    candidates = data.get("candidates") or []
    parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
    text_chunks: list[str] = []
    parsed_calls: list[ToolCall] = []
    for p in parts:
        if "text" in p and p["text"]:
            text_chunks.append(str(p["text"]))
        if "functionCall" in p:
            fc = p["functionCall"]
            parsed_calls.append(
                ToolCall(
                    id=f"gem_{uuid.uuid4().hex[:8]}",
                    name=str(fc.get("name") or ""),
                    arguments=dict(fc.get("args") or {}),
                )
            )
    return LLMResponse(
        text="\n".join(text_chunks).strip(),
        tool_calls=parsed_calls,
        provider_used="gemini",
    )


async def _call_anthropic(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    system_prompt: str,
    tools: list[dict[str, Any]],
) -> LLMResponse:
    formatted: list[dict[str, Any]] = []
    pending_results: list[dict[str, Any]] = []

    def flush_results() -> None:
        nonlocal pending_results
        if pending_results:
            formatted.append({"role": "user", "content": pending_results})
            pending_results = []

    for m in messages:
        role = m.get("role")
        if role == "tool":
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id") or "",
                    "content": str(m.get("content") or "{}"),
                }
            )
            continue

        flush_results()
        if role == "user":
            formatted.append({"role": "user", "content": str(m.get("content") or "")})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": str(m["content"])})
            for tc in m.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc.get("arguments") or {},
                    }
                )
            if blocks:
                formatted.append({"role": "assistant", "content": blocks})
    flush_results()

    payload = {
        "model": model,
        "max_tokens": 2048,
        "system": system_prompt,
        "messages": formatted,
        "tools": [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            }
            for t in tools
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return LLMResponse(
            text=f"Anthropic API 호출 중 오류가 발생했습니다: {exc}",
            provider_used="anthropic",
            error=str(exc),
        )

    text_chunks: list[str] = []
    parsed_calls: list[ToolCall] = []
    for block in data.get("content") or []:
        btype = block.get("type")
        if btype == "text":
            text_chunks.append(str(block.get("text") or ""))
        elif btype == "tool_use":
            parsed_calls.append(
                ToolCall(
                    id=str(block.get("id") or f"ant_{uuid.uuid4().hex[:8]}"),
                    name=str(block.get("name") or ""),
                    arguments=dict(block.get("input") or {}),
                )
            )
    return LLMResponse(
        text="\n".join(text_chunks).strip(),
        tool_calls=parsed_calls,
        provider_used="anthropic",
    )


async def chat_with_tools(
    *,
    messages: list[dict[str, Any]],
    system_prompt: str,
    tools: list[dict[str, Any]],
    settings: dict[str, Any],
) -> LLMResponse:
    """Invoke the resolved LLM provider with tool definitions."""
    provider = resolve_active_provider(settings)

    if provider == "demo":
        return _run_demo_mode(messages)

    if provider == "gemini":
        key = (settings.get("gemini_api_key") or "").strip()
        if not key:
            return LLMResponse(
                text="Gemini API 키가 설정되지 않았습니다. 설정에서 키를 입력하거나 모드를 auto/demo로 변경하세요.",
                provider_used="gemini",
                error="missing_api_key",
            )
        return await _call_gemini(
            api_key=key,
            model=settings.get("gemini_model") or "gemini-2.5-flash",
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
        )

    if provider == "openai":
        key = (settings.get("openai_api_key") or "").strip()
        if not key:
            return LLMResponse(
                text="OpenAI API 키가 설정되지 않았습니다. 설정에서 키를 입력해 주세요.",
                provider_used="openai",
                error="missing_api_key",
            )
        return await _call_openai_compatible(
            base_url="https://api.openai.com/v1",
            api_key=key,
            model=settings.get("openai_model") or "gpt-4.1-mini",
            provider_label="openai",
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
        )

    if provider == "anthropic":
        key = (settings.get("anthropic_api_key") or "").strip()
        if not key:
            return LLMResponse(
                text="Anthropic API 키가 설정되지 않았습니다. 설정에서 키를 입력해 주세요.",
                provider_used="anthropic",
                error="missing_api_key",
            )
        return await _call_anthropic(
            api_key=key,
            model=settings.get("anthropic_model") or "claude-sonnet-4-20250514",
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
        )

    if provider == "ollama":
        base = (settings.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
        return await _call_openai_compatible(
            base_url=f"{base}/v1",
            api_key="ollama",
            model=settings.get("ollama_model") or "qwen2.5:7b",
            provider_label="ollama",
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
        )

    return _run_demo_mode(messages)
