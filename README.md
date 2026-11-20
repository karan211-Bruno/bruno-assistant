# Bruno AI (`bruno-assistant`)

Tailscale로 연결된 `bruno-sony` 리눅스 서버에 상시 띄워 두고, **아이폰 · 안드로이드 · 아이패드 · 맥 · PC** 어디서나 동일한 대화·메모리·전화·일정·브라우저 제어권을 공유하는 셀프호스팅 개인 AI 비서(PWA)입니다.

## 주요 기능

1. **크로스플랫폼 PWA + 실시간 SSE 동기화**
   - PC에서 시킨 일을 폰 푸시/토스트로 받고, 태블릿에서 이어서 승인할 수 있습니다.
   - 다크/라이트 모드 자동 적응 및 모바일 하단 탭 바 / 데스크톱 사이드바 지원.
2. **AI 전화 발신 대행 (`simulation` / `vapi` / `retell` / `twilio_realtime`)**
   - 기본적으로 **승인 카드(Human-in-the-Loop)**를 먼저 띄워 잘못 전화를 거는 사고를 방지합니다.
   - API 키 없이도 `simulation` 모드로 전화 발신·대화 녹취·요약 흐름 전체를 즉시 테스트할 수 있습니다.
3. **장기 기억 버블 · 연락처 · 일정 알림**
   - 취향·가족·자주 쓰는 연락처·기한 있는 할 일을 SQLite에 저장하고, 15초 주기 백그라운드 스케줄러가 알림을 띄워줍니다.
4. **샌드박스 브라우저 (직접 로그인/결제 이어받기)**
   - Playwright(선택 설치) 기반 브라우저 화면을 웹 UI에서 그대로 보고, 폰/태블릿으로 클릭·텍스트 입력을 대신 처리한 뒤 AI에게 넘길 수 있습니다.

---

## 1. 로컬(이 Mac)에서 바로 테스트하기

```bash
cd /Users/newborn/.gemini/antigravity/scratch/bruno-assistant
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

브라우저에서 `http://127.0.0.1:8765` 열기.

---

## 2. `bruno-sony` 서버로 1분 배포하기

### 1단계: `bruno-sony`에 SSH 접근 허용 (최초 1회)
`bruno-sony` 컴퓨터 터미널에서 아래 명령 중 하나를 실행합니다:
```bash
# 방법 A (가장 간단): Tailscale SSH 활성화
sudo tailscale up --ssh

# 방법 B: 일반 SSH 공개키 등록 (Mac에서 실행)
ssh-copy-id <계정명>@bruno-sony
```

### 2단계: 배포 스크립트 실행 (이 Mac에서)
```bash
cd /Users/newborn/.gemini/antigravity/scratch/bruno-assistant
./scripts/deploy_bruno_sony.sh <계정명>@bruno-sony
```
*(브라우저 자동화까지 설치하려면 끝에 `--with-browser` 추가)*

배포가 끝나면 Tailscale이 켜진 폰·태블릿·PC에서 아래 주소로 접속합니다:
- `https://bruno-sony.tail617e7a.ts.net`

### 폰 / 태블릿 앱처럼 설치하기 (PWA)
- **iPhone / iPad (Safari):** 주소 접속 → 공유 버튼 → **홈 화면에 추가**
- **Android (Chrome):** 주소 접속 → 메뉴(⋮) → **앱 설치**

---

## 3. 실제 AI 모델 & 전화 연동 안내

웹 UI 우측 상단 **⚙️ 설정**에서 바로 입력·저장할 수 있습니다.

- **AI 두뇌 (LLM):**
  - 키 없이도 기본 **로컬 데모 모드**로 연락처·기억·알림·웹검색·전화 시뮬레이션이 동작합니다.
  - Gemini / OpenAI / Anthropic(Claude) API 키 또는 로컬 Ollama 주소를 입력하면 범용 에이전트로 동작합니다.
- **실제 한국 AI 전화 발신:**
  - **Vapi / Retell:** 발신번호 ID와 API 키 입력만으로 즉시 발신 및 실시간 녹취·요약 가능 (백그라운드 폴링도 지원).
  - **Twilio + OpenAI Realtime:** 한국 발신/국제전화가 가능한 Twilio 번호와 OpenAI API 키 연결. 외부 Twilio 웹훅/웹소켓 수신을 위해 `bruno-sony`에서 아래 경로만 Funnel을 열어주세요:
    ```bash
    sudo tailscale funnel --bg --set-path=/api/telephony http://127.0.0.1:8765/api/telephony
    sudo tailscale funnel --bg --set-path=/ws/telephony http://127.0.0.1:8765/ws/telephony
    ```
