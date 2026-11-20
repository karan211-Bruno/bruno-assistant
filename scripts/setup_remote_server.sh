#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WITH_BROWSER="false"
for arg in "$@"; do
  if [[ "$arg" == "--with-browser" ]]; then
    WITH_BROWSER="true"
  fi
done

echo "==> [1/5] Bruno AI 서버 경로: $APP_DIR"
cd "$APP_DIR"
mkdir -p data

if ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "==> uv 설치 시도 중..."
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh || true
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  fi
fi

echo "==> [2/5] Python 가상환경 및 의존성 설치"
if command -v uv >/dev/null 2>&1; then
  uv venv --allow-existing .venv
  if [[ "$WITH_BROWSER" == "true" ]]; then
    uv pip install --python .venv/bin/python -e ".[browser]"
    .venv/bin/playwright install chromium || true
  else
    uv pip install --python .venv/bin/python -e .
  fi
else
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  if [[ "$WITH_BROWSER" == "true" ]]; then
    .venv/bin/pip install -e ".[browser]"
    .venv/bin/playwright install chromium || true
  else
    .venv/bin/pip install -e .
  fi
fi

echo "==> [3/5] systemd 사용자 서비스 등록 (bruno-assistant.service)"
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"
cat > "$SYSTEMD_DIR/bruno-assistant.service" <<EOF
[Unit]
Description=Bruno AI Personal Assistant
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8765
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user enable --now bruno-assistant.service
  systemctl --user restart bruno-assistant.service
else
  echo "경고: systemctl을 찾을 수 없어 백그라운드 nohup으로 실행합니다."
  pkill -f "uvicorn backend.main:app" || true
  nohup "$APP_DIR/.venv/bin/uvicorn" backend.main:app --host 127.0.0.1 --port 8765 > "$APP_DIR/data/server.log" 2>&1 &
fi

if command -v loginctl >/dev/null 2>&1; then
  loginctl enable-linger "$USER" 2>/dev/null || echo "참고: 로그아웃 후에도 유지하려면 bruno-sony에서 'sudo loginctl enable-linger $USER'를 한 번 실행하세요."
fi

echo "==> [4/5] Tailscale Serve HTTPS 연결 시도"
if command -v tailscale >/dev/null 2>&1; then
  if tailscale serve --bg http://127.0.0.1:8765 >/dev/null 2>&1; then
    echo "Tailscale Serve 활성화 완료 -> https://bruno-sony.tail617e7a.ts.net"
  elif command -v sudo >/dev/null 2>&1 && sudo -n tailscale serve --bg http://127.0.0.1:8765 >/dev/null 2>&1; then
    echo "Tailscale Serve 활성화 완료 -> https://bruno-sony.tail617e7a.ts.net"
  else
    echo "참고: Tailscale Serve 권한이 필요합니다. bruno-sony 터미널에서 아래 명령을 실행하세요:"
    echo "  sudo tailscale serve --bg http://127.0.0.1:8765"
  fi
fi

echo "==> [5/5] 상태 확인"
sleep 1.5
if command -v curl >/dev/null 2>&1; then
  curl -fsS http://127.0.0.1:8765/api/health || true
  echo ""
fi

cat <<'INFO'

========================================================================
[선택 안내]
1) 노트북 덮개를 닫아도 꺼지지 않게 하려면 bruno-sony에서 설정하세요:
   sudo sed -i 's/^#*HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf && sudo systemctl restart systemd-logind

2) Twilio / Vapi / Retell 외부 전화 웹훅을 bruno-sony로 직접 받으려면:
   sudo tailscale funnel --bg --set-path=/api/telephony http://127.0.0.1:8765/api/telephony
   sudo tailscale funnel --bg --set-path=/ws/telephony http://127.0.0.1:8765/ws/telephony
========================================================================
INFO
