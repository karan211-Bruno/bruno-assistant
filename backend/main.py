"""FastAPI server entrypoint for Bruno AI."""

from __future__ import annotations

import asyncio
import hmac
import json
import re
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import telephony
from backend.agent import run_agent_turn
from backend.config import get_access_token, get_settings, save_settings
from backend.db import (
    PROJECT_ROOT,
    db_session,
    get_data_dir,
    init_db,
    new_id,
    now_iso,
)
from backend.event_bus import event_bus
from backend.llm import resolve_active_provider
from backend.scheduler import scheduler_loop
from backend.tools import browser_tools, contact_tools, memory_tools, task_tools

STATIC_DIR = PROJECT_ROOT / "static"
(STATIC_DIR / "css").mkdir(parents=True, exist_ok=True)
(STATIC_DIR / "js").mkdir(parents=True, exist_ok=True)

FILENAME_RE = re.compile(r"^[a-zA-Z0-9_-]+\.png$")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize SQLite DB, launch scheduler background loop, and shut down cleanly."""
    init_db()
    stop_event = asyncio.Event()
    sched_task = asyncio.create_task(scheduler_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        sched_task.cancel()
        try:
            await sched_task
        except asyncio.CancelledError:
            pass
        await browser_tools.browser_manager.shutdown()


app = FastAPI(title="Bruno AI Assistant", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def verify_access_token(request: Request, call_next):
    """Require BRUNO_ACCESS_TOKEN for /api/* routes when configured."""
    token = get_access_token()
    path = request.url.path
    if (
        token
        and request.method != "OPTIONS"
        and path.startswith("/api/")
        and path != "/api/health"
        and not path.startswith("/api/telephony/")
    ):
        auth_header = request.headers.get("authorization", "")
        bearer = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
        query_token = (request.query_params.get("token") or "").strip()
        provided = bearer or query_token
        if not provided or not hmac.compare_digest(provided, token):
            return JSONResponse(status_code=401, content={"detail": "인증 토큰이 필요합니다."})
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/css", StaticFiles(directory=str(STATIC_DIR / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(STATIC_DIR / "js")), name="js")


# ---- Pydantic request schemas ----


class ConversationCreate(BaseModel):
    title: str = Field(default="새 대화", max_length=100)


class ChatRequest(BaseModel):
    conversation_id: str = Field(default="default")
    message: str = Field(..., min_length=1, max_length=8000)


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    note: str = Field(default="", max_length=500)


class DirectCallRequest(BaseModel):
    recipient_name: str = Field(default="", max_length=100)
    phone_number: str = Field(..., min_length=3, max_length=40)
    goal: str = Field(..., min_length=1, max_length=1000)
    script: str = Field(default="", max_length=2000)
    provider: str = Field(default="", max_length=40)
    conversation_id: str = Field(default="")


class MemoryCreate(BaseModel):
    category: str = Field(default="general", max_length=40)
    content: str = Field(..., min_length=1, max_length=2000)
    tags: str = Field(default="", max_length=200)


class ContactCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., min_length=3, max_length=40)
    relation: str = Field(default="", max_length=60)
    notes: str = Field(default="", max_length=500)


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    due_at: str = Field(default="", max_length=64)
    kind: str = Field(default="task", max_length=30)
    notes: str = Field(default="", max_length=1000)


class TaskPatch(BaseModel):
    title: str | None = None
    due_at: str | None = None
    status: str | None = None
    notes: str | None = None


class BrowserNavigateRequest(BaseModel):
    url: str = Field(..., min_length=4, max_length=2000)


class BrowserClickRequest(BaseModel):
    selector: str | None = None
    x: float | None = None
    y: float | None = None


class BrowserTypeRequest(BaseModel):
    selector: str = ""
    text: str = ""
    press_enter: bool = False


# ---- Health & Settings ----


@app.get("/api/health")
def api_health() -> dict[str, Any]:
    settings = get_settings(include_secrets=True)
    return {
        "ok": True,
        "version": "0.2.0",
        "auth_required": bool(get_access_token()),
        "host": socket.gethostname(),
        "tailscale_hint": "https://bruno-sony.tail617e7a.ts.net",
        "active_llm_provider": resolve_active_provider(settings),
        "telephony_provider": settings.get("telephony_provider", "simulation"),
        "playwright_available": browser_tools.is_playwright_available(),
    }


@app.get("/api/settings")
def api_get_settings() -> dict[str, Any]:
    return get_settings(include_secrets=False)


@app.put("/api/settings")
def api_put_settings(payload: dict[str, Any]) -> dict[str, Any]:
    return save_settings(payload)


# ---- Conversations & Chat ----


@app.get("/api/conversations")
def api_list_conversations() -> dict[str, Any]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
    return {"conversations": [dict(r) for r in rows]}


@app.post("/api/conversations")
def api_create_conversation(req: ConversationCreate) -> dict[str, Any]:
    cid = new_id("conv")
    ts = now_iso()
    title = req.title.strip() or "새 대화"
    with db_session() as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (cid, title, ts, ts),
        )
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
    return {"conversation": dict(row) if row else {"id": cid, "title": title}}


@app.delete("/api/conversations/{conv_id}")
def api_delete_conversation(conv_id: str) -> dict[str, Any]:
    with db_session() as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        remaining = conn.execute("SELECT COUNT(*) AS cnt FROM conversations").fetchone()
        if remaining and remaining["cnt"] == 0:
            ts = now_iso()
            conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("default", "메인 비서", ts, ts),
            )
    return {"ok": True}


@app.get("/api/conversations/{conv_id}/messages")
def api_get_messages(conv_id: str) -> dict[str, Any]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conv_id,),
        ).fetchall()
    messages = []
    for r in rows:
        item = dict(r)
        try:
            item["meta"] = json.loads(item.get("meta_json") or "{}")
        except Exception:
            item["meta"] = {}
        messages.append(item)
    return {"messages": messages}


@app.post("/api/chat")
async def api_chat(req: ChatRequest) -> dict[str, Any]:
    try:
        return await run_agent_turn(req.conversation_id, req.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---- Approvals (Human-in-the-loop) ----


def _deserialize_approval(row: Any) -> dict[str, Any]:
    item = dict(row)
    try:
        item["payload"] = json.loads(item.get("payload_json") or "{}")
    except Exception:
        item["payload"] = {}
    return item


@app.get("/api/approvals")
def api_list_approvals(status: str = "all") -> dict[str, Any]:
    with db_session() as conn:
        if status in {"pending", "approved", "rejected"}:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE status = ? ORDER BY created_at DESC LIMIT 50",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM approvals ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, created_at DESC LIMIT 50"
            ).fetchall()
    return {"approvals": [_deserialize_approval(r) for r in rows]}


@app.post("/api/approvals/{approval_id}/decide")
async def api_decide_approval(approval_id: str, req: ApprovalDecisionRequest) -> dict[str, Any]:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="승인 요청을 찾을 수 없습니다.")
        approval = _deserialize_approval(row)
        if approval["status"] != "pending":
            return {"ok": True, "approval": approval, "already_decided": True}
        ts = now_iso()
        conn.execute(
            "UPDATE approvals SET status = ?, decision_note = ?, decided_at = ? WHERE id = ?",
            (req.decision, req.note.strip(), ts, approval_id),
        )
        updated_row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()

    updated = _deserialize_approval(updated_row)
    event_bus.publish("approval_updated", {"approval": updated})

    created_call = None
    if req.decision == "approved" and updated["kind"] == "phone_call":
        payload = updated.get("payload") or {}
        created_call = telephony.create_call_record(
            phone_number=str(payload.get("phone_number") or ""),
            goal=str(payload.get("goal") or updated["description"]),
            recipient_name=str(payload.get("recipient_name") or ""),
            script=str(payload.get("script") or ""),
            provider=str(payload.get("provider") or "") or None,
            conversation_id=updated.get("conversation_id") or None,
        )
        created_call = await telephony.place_call(created_call["id"])

    return {"ok": True, "approval": updated, "call": created_call}


# ---- Outbound Calls & Telephony Webhooks ----


@app.get("/api/calls")
def api_list_calls() -> dict[str, Any]:
    return {"calls": telephony.list_calls()}


@app.post("/api/calls")
async def api_create_call(req: DirectCallRequest) -> dict[str, Any]:
    call = telephony.create_call_record(
        phone_number=req.phone_number,
        goal=req.goal,
        recipient_name=req.recipient_name,
        script=req.script,
        provider=req.provider or None,
        conversation_id=req.conversation_id or None,
    )
    dispatched = await telephony.place_call(call["id"])
    return {"ok": True, "call": dispatched}


@app.get("/api/calls/{call_id}")
def api_get_call(call_id: str) -> dict[str, Any]:
    call = telephony.get_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="통화 기록을 찾을 수 없습니다.")
    return {"call": call}


@app.api_route("/api/telephony/twilio/twiml/{call_id}", methods=["GET", "POST"])
def api_twilio_twiml(call_id: str, request: Request) -> Response:
    settings = get_settings(include_secrets=False)
    base = (settings.get("public_base_url") or str(request.base_url)).rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://") :]
    else:
        ws_base = f"wss://{base}"
    ws_url = f"{ws_base}/ws/telephony/twilio/{call_id}"
    xml = telephony.build_twilio_twiml(ws_url)
    return Response(content=xml, media_type="application/xml")


@app.post("/api/telephony/twilio/status/{call_id}")
async def api_twilio_status(call_id: str, request: Request) -> dict[str, Any]:
    call_status = ""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
        call_status = str(data.get("CallStatus") or "")
    else:
        form = await request.form()
        call_status = str(form.get("CallStatus") or "")
    updated = telephony.handle_twilio_status(call_id, call_status)
    return {"ok": True, "call": updated}


@app.websocket("/ws/telephony/twilio/{call_id}")
async def ws_twilio_stream(websocket: WebSocket, call_id: str) -> None:
    await telephony.handle_twilio_media_stream(websocket, call_id)


@app.post("/api/telephony/vapi/webhook")
async def api_vapi_webhook(request: Request) -> dict[str, Any]:
    payload = await request.json()
    return telephony.handle_vapi_webhook(payload)


@app.post("/api/telephony/retell/webhook")
async def api_retell_webhook(request: Request) -> dict[str, Any]:
    payload = await request.json()
    return telephony.handle_retell_webhook(payload)


# ---- Memories, Contacts, Tasks ----


@app.get("/api/memories")
def api_list_memories(q: str = "") -> dict[str, Any]:
    return {"memories": memory_tools.list_memories(query=q)}


@app.post("/api/memories")
def api_create_memory(req: MemoryCreate) -> dict[str, Any]:
    return memory_tools.remember_fact(req.category, req.content, req.tags)


@app.delete("/api/memories/{memory_id}")
def api_delete_memory(memory_id: str) -> dict[str, Any]:
    return memory_tools.forget_memory(memory_id)


@app.get("/api/contacts")
def api_list_contacts(q: str = "") -> dict[str, Any]:
    return {"contacts": contact_tools.list_contacts(query=q)}


@app.post("/api/contacts")
def api_create_contact(req: ContactCreate) -> dict[str, Any]:
    return contact_tools.save_contact(req.name, req.phone, req.relation, req.notes)


@app.delete("/api/contacts/{contact_id}")
def api_delete_contact(contact_id: str) -> dict[str, Any]:
    return contact_tools.delete_contact(contact_id)


@app.get("/api/tasks")
def api_list_tasks(status: str = "all") -> dict[str, Any]:
    return task_tools.list_tasks(status=status)


@app.post("/api/tasks")
def api_create_task(req: TaskCreate) -> dict[str, Any]:
    return task_tools.create_task(req.title, req.due_at, req.kind, req.notes)


@app.patch("/api/tasks/{task_id}")
def api_patch_task(task_id: str, req: TaskPatch) -> dict[str, Any]:
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    res = task_tools.update_task(task_id, updates)
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail=res.get("error", "Not found"))
    return res


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: str) -> dict[str, Any]:
    return task_tools.delete_task(task_id)


# ---- Sandbox Browser ----


@app.get("/api/browser/state")
def api_browser_state() -> dict[str, Any]:
    return browser_tools.browser_manager.get_state()


@app.post("/api/browser/navigate")
async def api_browser_navigate(req: BrowserNavigateRequest) -> dict[str, Any]:
    return await browser_tools.browser_manager.open_url(req.url)


@app.post("/api/browser/click")
async def api_browser_click(req: BrowserClickRequest) -> dict[str, Any]:
    if req.selector:
        return await browser_tools.browser_manager.click_selector(req.selector)
    if req.x is not None and req.y is not None:
        return await browser_tools.browser_manager.click_point(req.x, req.y)
    raise HTTPException(status_code=400, detail="selector 또는 x, y 좌표가 필요합니다.")


@app.post("/api/browser/type")
async def api_browser_type(req: BrowserTypeRequest) -> dict[str, Any]:
    return await browser_tools.browser_manager.type_text(
        req.selector, req.text, press_enter=req.press_enter
    )


@app.post("/api/browser/screenshot")
async def api_browser_screenshot() -> dict[str, Any]:
    return await browser_tools.browser_manager.screenshot()


@app.get("/api/browser/screenshot/{filename}")
def api_browser_screenshot_file(filename: str) -> FileResponse:
    if not FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid screenshot filename")
    path = Path(get_data_dir() / "screenshots" / filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path, media_type="image/png")


# ---- SSE Event Stream ----


@app.get("/api/events")
async def api_events() -> StreamingResponse:
    return StreamingResponse(
        event_bus.subscribe(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---- PWA Frontend Entrypoints ----


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest")
def serve_manifest() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
def serve_sw() -> FileResponse:
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


@app.get("/icon.svg")
def serve_icon() -> FileResponse:
    return FileResponse(STATIC_DIR / "icon.svg", media_type="image/svg+xml")
