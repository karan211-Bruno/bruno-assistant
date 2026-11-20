# Bruno AI (`bruno-assistant`) v0.2.0

**Tailscale 없이도 단독 실행**되고, 원할 경우 **Cloudflare Tunnel · Render · Docker · 로컬 PC · Tailscale**로 다기기 동기화까지 지원하는 **6대 플랫폼 통합 개인 AI 비서**(Android · iPhone · iPad · macOS · Windows · Linux)입니다.

---

## 두 가지 실행 모드 (선택 가능)

1. **📱 기기 단독 모드 (기본값 · Tailscale/서버 불필요)**
   - 앱 설치 후 바로 실행 가능하며, 대화·기억·연락처·일정 알림·웹 검색·AI 전화(시뮬레이션 및 Vapi/Retell)가 기기 자체에서 동작합니다.
2. **☁️ 원격 서버 동기화 모드 (선택 · 다기기 실시간 공유)**
   - 폰·태블릿·PC 간 실시간 SSE 동기화, 샌드박스 브라우저(Playwright), Twilio 실시간 음성 브리지를 쓰고 싶을 때 사용합니다.
   - **Tailscale 없이 공용 HTTPS 연결:**
     - **Cloudflare Tunnel:** 서버에서 `./scripts/enable_public_access.sh --cloudflare` 실행 (토큰 `BRUNO_ACCESS_TOKEN` 자동 보호)
     - **Render.com / Docker 클라우드:** 포함된 `Dockerfile` / `render.yaml`로 1클릭 배포
     - **Tailscale Funnel:** 서버(`bruno-sony`)에만 Tailscale을 켜고 폰/태블릿은 Tailscale 없이 접속 (`./scripts/enable_public_access.sh --funnel`)

---

## 주요 기능

- **AI 전화 발신 대행 (`simulation` / `vapi` / `retell` / `twilio_realtime`)**
  - 발신 전 **승인 카드(Human-in-the-Loop)**로 안전하게 확인 후 통화를 겁니다.
- **장기 기억 버블 · 연락처 · 일정 알림**
  - 취향/가족/연락처/할 일을 저장하고 정해진 시각에 알림을 보냅니다.
- **크로스플랫폼 UI & 한국어 음성 입력**
  - 다크/라이트 모드 자동 적응, 모바일 하단 탭 바 / 데스크톱 사이드바, 웹/네이티브 음성 인식 지원.

---

## 로컬 서버 실행 & 6대 플랫폼 빌드

```bash
# 로컬 Python 서버 실행
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8765

# 6대 플랫폼(macOS, Windows, Linux, Android, iPhone/iPad) 전체 재빌드
./scripts/build_all_apps.sh
```
