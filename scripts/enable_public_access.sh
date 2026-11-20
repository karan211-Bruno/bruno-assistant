#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
MODE="${1:-auto}"

touch "$ENV_FILE"

if ! grep -qE '^BRUNO_ACCESS_TOKEN=.+$' "$ENV_FILE"; then
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  printf '\nBRUNO_ACCESS_TOKEN=%s\n' "$TOKEN" >> "$ENV_FILE"
  echo "새 접근 토큰(BRUNO_ACCESS_TOKEN)을 .env에 생성했습니다: $TOKEN"
else
  TOKEN="$(grep -E '^BRUNO_ACCESS_TOKEN=' "$ENV_FILE" | head -n1 | cut -d= -f2-)"
  echo "기존 .env 접근 토큰을 사용합니다: $TOKEN"
fi

if command -v systemctl >/dev/null 2>&1 && systemctl --user list-unit-files bruno-assistant.service >/dev/null 2>&1; then
  systemctl --user restart bruno-assistant.service || true
fi

echo ""
echo "================================================================"
echo "Tailscale 없이 외부(폰·태블릿·PC)에서 접속하려면 아래 방법 중 하나를 선택하세요:"
echo ""
echo "방법 1) Cloudflare Quick Tunnel (서버·클라이언트 모두 Tailscale 불필요):"
echo "  cloudflared tunnel --url http://127.0.0.1:8765"
echo ""
echo "방법 2) Tailscale Funnel (bruno-sony에만 Tailscale 설치, 폰·태블릿은 Tailscale 불필요):"
echo "  sudo tailscale funnel --bg http://127.0.0.1:8765"
echo ""
echo "앱 설정(⚙️) → 원격 서버 모드에서 위 HTTPS 주소와 아래 토큰을 입력하세요:"
echo "  토큰: $TOKEN"
echo "================================================================"

if [[ "$MODE" == "--cloudflare" ]]; then
  if command -v cloudflared >/dev/null 2>&1; then
    exec cloudflared tunnel --url http://127.0.0.1:8765
  else
    echo "오류: cloudflared가 설치되어 있지 않습니다."
    exit 1
  fi
elif [[ "$MODE" == "--funnel" ]]; then
  exec sudo tailscale funnel --bg http://127.0.0.1:8765
fi
