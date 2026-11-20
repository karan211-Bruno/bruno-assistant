#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET=""
BROWSER_FLAG=""

for arg in "$@"; do
  if [[ "$arg" == "--with-browser" ]]; then
    BROWSER_FLAG="--with-browser"
  elif [[ -z "$TARGET" ]]; then
    TARGET="$arg"
  fi
done

if [[ -z "$TARGET" ]]; then
  TARGET="bruno-sony"
fi

echo "==> [1/3] SSH 연결 확인: $TARGET"
if ! ssh -o BatchMode=yes -o ConnectTimeout=6 "$TARGET" "echo connected" >/dev/null 2>&1; then
  cat <<EOF

❌ '$TARGET'에 SSH 자동 로그인을 할 수 없습니다 (Permission denied).

아래 두 가지 중 하나를 bruno-sony에서 한 번만 실행해 주세요:

방법 A (가장 간편 · Tailscale SSH 켜기):
  bruno-sony 터미널에서 실행:
    sudo tailscale up --ssh

방법 B (SSH 공개키 복사):
  이 맥에서 실행 (비밀번호 입력):
    ssh-copy-id $TARGET

완료 후 다시 실행하세요:
  ./scripts/deploy_bruno_sony.sh <bruno-sony계정명>@bruno-sony $BROWSER_FLAG
EOF
  exit 1
fi

echo "==> [2/3] 프로젝트 파일 동기화 (data/ 및 .env 보존)"
rsync -az --delete \
  --exclude ".venv/" \
  --exclude "data/" \
  --exclude ".env" \
  --exclude "__pycache__/" \
  --exclude "*.pyc" \
  --exclude ".pytest_cache/" \
  --exclude ".git/" \
  "$PROJECT_DIR/" "$TARGET:~/bruno-assistant/"

echo "==> [3/3] bruno-sony 서버 설정 및 실행"
ssh "$TARGET" "bash ~/bruno-assistant/scripts/setup_remote_server.sh $BROWSER_FLAG"

echo ""
echo "🎉 배포 완료!"
echo "   모든 Tailscale 기기(폰·태블릿·PC)에서 접속: https://bruno-sony.tail617e7a.ts.net"
echo "   (만약 Tailscale Serve가 아직 안 열렸다면 bruno-sony에서 'sudo tailscale serve --bg http://127.0.0.1:8765' 실행)"
