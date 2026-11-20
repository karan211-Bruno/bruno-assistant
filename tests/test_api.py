"""Automated integration tests for Bruno AI backend and PWA assets."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import app
from backend.tools.web_tools import validate_public_url


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_bruno.db"
    monkeypatch.setenv("BRUNO_DB_PATH", str(db_file))
    monkeypatch.setenv("BRUNO_SIM_SCALE", "0.02")
    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoint(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    for key in (
        "host",
        "tailscale_hint",
        "active_llm_provider",
        "telephony_provider",
        "playwright_available",
    ):
        assert key in data


def test_settings_secret_masking_and_preservation(client: TestClient) -> None:
    put_resp = client.put(
        "/api/settings",
        json={
            "user_name": "테스트유저",
            "gemini_api_key": "AIzaSecretTestKey9876",
            "openai_api_key": "sk-test-openai-4321",
        },
    )
    assert put_resp.status_code == 200
    masked = put_resp.json()
    assert masked["user_name"] == "테스트유저"
    assert masked["gemini_api_key"] == "****9876"
    assert masked["has_gemini_api_key"] is True
    assert masked["openai_api_key"] == "****4321"

    # Saving non-secret fields or masked/empty secret placeholders must NOT wipe existing secrets
    put_resp2 = client.put(
        "/api/settings",
        json={
            "user_name": "브루노2",
            "gemini_api_key": "****9876",
            "openai_api_key": "",
        },
    )
    assert put_resp2.status_code == 200
    assert put_resp2.json()["user_name"] == "브루노2"
    assert put_resp2.json()["gemini_api_key"] == "****9876"
    assert put_resp2.json()["openai_api_key"] == "****4321"

    raw = get_settings(include_secrets=True)
    assert raw["gemini_api_key"] == "AIzaSecretTestKey9876"
    assert raw["openai_api_key"] == "sk-test-openai-4321"


def test_crud_memories_contacts_tasks(client: TestClient) -> None:
    # Memories
    m_create = client.post(
        "/api/memories",
        json={"category": "preference", "content": "아메리카노는 아이스만 마심", "tags": "커피"},
    )
    assert m_create.status_code == 200
    mem_id = m_create.json()["memory"]["id"]
    m_list = client.get("/api/memories")
    assert any(m["id"] == mem_id for m in m_list.json()["memories"])
    assert client.delete(f"/api/memories/{mem_id}").json()["ok"] is True

    # Contacts
    c_create = client.post(
        "/api/contacts",
        json={"name": "단골파스타", "phone": "02-555-1234", "relation": "식당"},
    )
    assert c_create.status_code == 200
    ct_id = c_create.json()["contact"]["id"]
    assert any(c["id"] == ct_id for c in client.get("/api/contacts").json()["contacts"])
    assert client.delete(f"/api/contacts/{ct_id}").json()["ok"] is True

    # Tasks
    t_create = client.post(
        "/api/tasks",
        json={"title": "비타민 챙겨먹기", "kind": "task"},
    )
    assert t_create.status_code == 200
    task_id = t_create.json()["task"]["id"]
    t_patch = client.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    assert t_patch.status_code == 200
    assert t_patch.json()["task"]["status"] == "done"
    assert client.delete(f"/api/tasks/{task_id}").json()["ok"] is True


def test_demo_chat_tools_and_call_approval_lifecycle(client: TestClient) -> None:
    # Force demo mode explicitly for predictable local testing
    client.put("/api/settings", json={"llm_provider": "demo", "telephony_provider": "simulation"})

    # 1. Remember fact via chat
    r_mem = client.post(
        "/api/chat",
        json={"conversation_id": "default", "message": "나는 땅콩 알레르기가 있다고 기억해줘"},
    )
    assert r_mem.status_code == 200
    assert r_mem.json()["tool_trace"][0]["tool"] == "remember_fact"
    assert any("땅콩" in m["content"] for m in client.get("/api/memories").json()["memories"])

    # 2. Create task via chat
    r_task = client.post(
        "/api/chat",
        json={"conversation_id": "default", "message": "10분 뒤 스트레칭 하기 알림 등록해줘"},
    )
    assert r_task.status_code == 200
    assert r_task.json()["tool_trace"][0]["tool"] == "create_task"
    assert any("스트레칭" in t["title"] for t in client.get("/api/tasks").json()["tasks"])

    # 3. Request phone call via chat -> creates pending approval card
    r_call = client.post(
        "/api/chat",
        json={
            "conversation_id": "default",
            "message": "단골식당에 02-333-4444 오늘 저녁 7시 2명 예약 전화해줘",
        },
    )
    assert r_call.status_code == 200
    trace = r_call.json()["tool_trace"][0]
    assert trace["tool"] == "make_phone_call"
    approval_id = trace["result"]["approval_id"]

    approvals = client.get("/api/approvals?status=pending").json()["approvals"]
    assert any(a["id"] == approval_id for a in approvals)

    # 4. Approve call -> creates calls record and runs simulated conversation to completion
    decide = client.post(f"/api/approvals/{approval_id}/decide", json={"decision": "approved"})
    assert decide.status_code == 200
    call_id = decide.json()["call"]["id"]

    completed_call = None
    for _ in range(30):
        res = client.get(f"/api/calls/{call_id}").json()["call"]
        if res["status"] == "completed":
            completed_call = res
            break
        time.sleep(0.05)

    assert completed_call is not None
    assert len(completed_call["transcript"]) >= 4
    assert "[시뮬레이션]" in completed_call["transcript"][0]["text"]
    assert completed_call["summary"]


def test_twilio_twiml_generation_and_ssrf_guard(client: TestClient) -> None:
    client.put("/api/settings", json={"public_base_url": "https://bruno-sony.tail617e7a.ts.net"})
    twiml_resp = client.post("/api/telephony/twilio/twiml/call_test123")
    assert twiml_resp.status_code == 200
    assert "xml" in twiml_resp.headers["content-type"]

    root = ET.fromstring(twiml_resp.text)
    stream = root.find("./Connect/Stream")
    assert stream is not None
    assert stream.attrib["url"] == "wss://bruno-sony.tail617e7a.ts.net/ws/telephony/twilio/call_test123"

    ok_local, _ = validate_public_url("http://127.0.0.1:8765/api/health")
    assert ok_local is False
    ok_meta, _ = validate_public_url("http://169.254.169.254/latest/meta-data/")
    assert ok_meta is False


def test_static_pwa_assets_served(client: TestClient) -> None:
    for path in ("/", "/manifest.webmanifest", "/sw.js", "/icon.svg", "/css/app.css", "/js/app.js"):
        resp = client.get(path)
        assert resp.status_code == 200, f"Failed serving {path}"


def test_cors_headers_for_native_apps(client: TestClient) -> None:
    resp = client.get("/api/health", headers={"Origin": "capacitor://localhost"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"

    preflight = client.options(
        "/api/chat",
        headers={
            "Origin": "http://localhost",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") == "*"
