"""Web search and SSRF-safe webpage fetching tools."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def _is_forbidden_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str.split("%")[0])
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_public_url(url: str) -> tuple[bool, str]:
    """Ensure URL uses http(s) and does not resolve to local/private/metadata addresses."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False, "http 또는 https URL만 허용됩니다."
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "유효한 호스트명이 없습니다."
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".local"):
        return False, "내부 또는 로컬 네트워크 주소에는 접근할 수 없습니다."

    try:
        ipaddress.ip_address(host)
        if _is_forbidden_ip(host):
            return False, "사설/루프백 IP 주소에는 접근할 수 없습니다."
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                resolved_ip = info[4][0]
                if _is_forbidden_ip(str(resolved_ip)):
                    return False, "사설/내부 IP로 해석되는 주소에는 접근할 수 없습니다."
        except socket.gaierror:
            return False, f"호스트를 찾을 수 없습니다: {host}"

    return True, ""


def _unwrap_ddg_href(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg")
        if uddg:
            return unquote(uddg[0])
    return href


async def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web via DuckDuckGo HTML endpoint."""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "검색어가 비어 있습니다."}
    limit = max(1, min(int(max_results or 5), 10))
    try:
        async with httpx.AsyncClient(
            timeout=12.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"},
        ) as client:
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": q, "kl": "kr-kr"},
            )
            resp.raise_for_status()
    except Exception as exc:
        return {"ok": False, "error": f"웹 검색 요청에 실패했습니다: {exc}", "results": []}

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[dict[str, str]] = []
    for item in soup.select(".result"):
        link_el = item.select_one(".result__a")
        if not link_el:
            continue
        title = link_el.get_text(" ", strip=True)
        href = _unwrap_ddg_href(str(link_el.get("href") or ""))
        snippet_el = item.select_one(".result__snippet")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        if title and href:
            results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= limit:
            break

    return {"ok": True, "query": q, "count": len(results), "results": results}


async def fetch_webpage(url: str) -> dict[str, Any]:
    """Fetch a public webpage and extract clean readable text (max 4000 chars)."""
    ok, err = validate_public_url(url)
    if not ok:
        return {"ok": False, "error": err}

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = await client.get(url)
            final_ok, final_err = validate_public_url(str(resp.url))
            if not final_ok:
                return {"ok": False, "error": final_err}
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        return {"ok": False, "error": f"웹페이지를 불러오지 못했습니다: {exc}"}

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cleaned = "\n".join(lines)[:4000]
    return {"ok": True, "url": str(resp.url), "title": title, "content": cleaned}
