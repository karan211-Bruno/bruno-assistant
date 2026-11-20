"""Optional Playwright sandbox browser controller with Human-in-the-Loop takeover."""

from __future__ import annotations

import asyncio
import importlib.util
import time
from typing import Any

from backend.db import db_session, get_data_dir, get_default_conversation_id, new_id, now_iso
from backend.event_bus import event_bus
from backend.tools.web_tools import validate_public_url


def is_playwright_available() -> bool:
    """Return True if playwright package is importable."""
    return importlib.util.find_spec("playwright") is not None


class BrowserSessionManager:
    """Lazy-initialized headless Chromium session shared across browser tools and UI."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._last_url: str = ""
        self._last_title: str = ""
        self._last_screenshot_file: str = ""
        self._last_updated: str = ""

    def _screenshot_dir(self):
        path = get_data_dir() / "screenshots"
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _ensure_page(self) -> tuple[Any, str | None]:
        if not is_playwright_available():
            return None, (
                "Playwright가 설치되어 있지 않습니다. 서버에서 "
                "`uv pip install -e '.[browser]' && playwright install chromium` 실행 후 이용하세요."
            )
        if self._page is not None:
            return self._page, None

        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
            context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="ko-KR",
                timezone_id="Asia/Seoul",
            )
            self._page = await context.new_page()
            return self._page, None
        except Exception as exc:
            await self.shutdown()
            return None, f"Chromium 브라우저 시작 실패: {exc}"

    def get_state(self) -> dict[str, Any]:
        """Return current browser state without launching Chromium."""
        ts_bust = int(time.time() * 1000)
        screenshot_url = (
            f"/api/browser/screenshot/{self._last_screenshot_file}?t={ts_bust}"
            if self._last_screenshot_file
            else ""
        )
        return {
            "available": is_playwright_available(),
            "running": self._page is not None,
            "url": self._last_url,
            "title": self._last_title,
            "screenshot_url": screenshot_url,
            "updated_at": self._last_updated,
        }

    async def _capture_and_publish(self) -> dict[str, Any]:
        if self._page is None:
            return self.get_state()
        self._last_url = self._page.url or ""
        try:
            self._last_title = await self._page.title()
        except Exception:
            self._last_title = ""
        filename = "latest.png"
        out_path = self._screenshot_dir() / filename
        await self._page.screenshot(path=str(out_path), full_page=False)
        self._last_screenshot_file = filename
        self._last_updated = now_iso()
        state = self.get_state()
        event_bus.publish("browser_updated", {"browser": state})
        return state

    async def open_url(self, url: str) -> dict[str, Any]:
        """Navigate sandbox browser to a validated URL."""
        ok, err = validate_public_url(url)
        if not ok:
            return {"ok": False, "available": is_playwright_available(), "error": err}

        async with self._lock:
            page, err = await self._ensure_page()
            if page is None:
                return {"ok": False, "available": is_playwright_available(), "error": err}
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                await asyncio.sleep(0.5)
                state = await self._capture_and_publish()
                text_preview = await page.evaluate(
                    "() => (document.body?.innerText || '').slice(0, 1500)"
                )
                return {"ok": True, **state, "text_preview": text_preview}
            except Exception as exc:
                return {"ok": False, "available": is_playwright_available(), "error": f"페이지 이동 실패: {exc}"}

    async def click_selector(self, selector: str) -> dict[str, Any]:
        """Click an element matching CSS selector."""
        async with self._lock:
            page, err = await self._ensure_page()
            if page is None:
                return {"ok": False, "available": is_playwright_available(), "error": err}
            try:
                await page.locator(selector).first.click(timeout=8000)
                await asyncio.sleep(0.5)
                state = await self._capture_and_publish()
                return {"ok": True, **state}
            except Exception as exc:
                return {"ok": False, "available": is_playwright_available(), "error": f"요소 클릭 실패: {exc}"}

    async def click_point(self, x: float, y: float) -> dict[str, Any]:
        """Click viewport coordinates (used when user interacts via phone/tablet UI)."""
        async with self._lock:
            page, err = await self._ensure_page()
            if page is None:
                return {"ok": False, "available": is_playwright_available(), "error": err}
            try:
                await page.mouse.click(float(x), float(y))
                await asyncio.sleep(0.5)
                state = await self._capture_and_publish()
                return {"ok": True, **state}
            except Exception as exc:
                return {"ok": False, "available": is_playwright_available(), "error": f"좌표 클릭 실패: {exc}"}

    async def type_text(self, selector: str, text: str, press_enter: bool = False) -> dict[str, Any]:
        """Type text either into a CSS selector or into the currently focused element."""
        async with self._lock:
            page, err = await self._ensure_page()
            if page is None:
                return {"ok": False, "available": is_playwright_available(), "error": err}
            try:
                if (selector or "").strip():
                    await page.locator(selector).first.fill(text, timeout=8000)
                else:
                    await page.keyboard.type(text)
                if press_enter:
                    await page.keyboard.press("Enter")
                await asyncio.sleep(0.4)
                state = await self._capture_and_publish()
                return {"ok": True, **state}
            except Exception as exc:
                return {"ok": False, "available": is_playwright_available(), "error": f"텍스트 입력 실패: {exc}"}

    async def screenshot(self) -> dict[str, Any]:
        """Capture and publish a fresh screenshot."""
        async with self._lock:
            page, err = await self._ensure_page()
            if page is None:
                return {"ok": False, "available": is_playwright_available(), "error": err}
            try:
                state = await self._capture_and_publish()
                return {"ok": True, **state}
            except Exception as exc:
                return {"ok": False, "available": is_playwright_available(), "error": f"스크린샷 캡처 실패: {exc}"}

    async def shutdown(self) -> None:
        """Close Playwright resources cleanly on application shutdown."""
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._browser = None
        self._playwright = None


browser_manager = BrowserSessionManager()


async def request_human_takeover(
    reason: str,
    url: str = "",
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Pause and request the user to handle login/verification/payment in the Browser tab."""
    conv_id = conversation_id or get_default_conversation_id()
    approval_id = new_id("apr")
    ts = now_iso()
    target_url = (url or browser_manager.get_state().get("url") or "").strip()
    payload = {"reason": reason, "url": target_url}
    with db_session() as conn:
        conn.execute(
            """
            INSERT INTO approvals (id, conversation_id, kind, title, description, payload_json, status, created_at)
            VALUES (?, ?, 'browser_takeover', ?, ?, ?, 'pending', ?)
            """,
            (
                approval_id,
                conv_id,
                "브라우저 직접 조작 요청",
                reason,
                __import__("json").dumps(payload, ensure_ascii=False),
                ts,
            ),
        )
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    approval = dict(row) if row else {"id": approval_id}
    event_bus.publish("approval_created", {"approval": approval})
    return {
        "ok": True,
        "status": "awaiting_user_takeover",
        "approval_id": approval_id,
        "message": "사용자에게 브라우저 직접 로그인/승인을 요청했습니다.",
    }
