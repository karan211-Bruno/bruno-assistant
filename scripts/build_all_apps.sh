#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> [1/6] 아이콘 생성 및 웹 에셋 동기화"
python3 "$ROOT/scripts/generate_icons.py"
"$ROOT/scripts/sync_web_assets.sh"

echo "==> [2/6] Capacitor Android / iOS 웹 에셋 동기화"
(cd "$ROOT/apps/mobile" && npx cap sync)
python3 "$ROOT/scripts/generate_icons.py"

echo "==> [3/6] 데스크톱 앱 빌드 (macOS / Windows / Linux)"
export CSC_IDENTITY_AUTO_DISCOVERY=false
(
  cd "$ROOT/apps/desktop"
  npm run build:mac
  npm run build:win
  if ! npm run build:linux; then
    echo "경고: AppImage 생성에 실패하여 Linux tar.gz 단독 빌드로 진행합니다."
    npx electron-builder --linux tar.gz --x64
  fi
)

echo "==> [4/6] 안드로이드 APK 빌드"
if [[ -d "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home" ]]; then
  export JAVA_HOME="/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
  export PATH="$JAVA_HOME/bin:$PATH"
fi
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-$ANDROID_HOME}"
(cd "$ROOT/apps/mobile/android" && ./gradlew assembleDebug)

echo "==> [5/6] iPhone / iPad 유니버설 Xcode 빌드 검증"
if command -v xcodebuild >/dev/null 2>&1; then
  (
    cd "$ROOT/apps/mobile/ios/App"
    xcodebuild -project App.xcodeproj -scheme App -sdk iphonesimulator -configuration Debug CODE_SIGNING_ALLOWED=NO build | tail -n 10
  )
fi

echo "==> [6/6] dist-apps/ 산출물 정리"
mkdir -p "$ROOT/dist-apps/macOS" "$ROOT/dist-apps/Windows" "$ROOT/dist-apps/Linux" "$ROOT/dist-apps/Android" "$ROOT/dist-apps/iPhone-iPad"

cp -f "$ROOT/apps/desktop/dist/"*-mac.zip "$ROOT/dist-apps/macOS/" 2>/dev/null || true
cp -f "$ROOT/apps/desktop/dist/"*.dmg "$ROOT/dist-apps/macOS/" 2>/dev/null || true
cp -f "$ROOT/apps/desktop/dist/"*win*.zip "$ROOT/dist-apps/Windows/" 2>/dev/null || true
cp -f "$ROOT/apps/desktop/dist/"*.AppImage "$ROOT/dist-apps/Linux/" 2>/dev/null || true
cp -f "$ROOT/apps/desktop/dist/"*.tar.gz "$ROOT/dist-apps/Linux/" 2>/dev/null || true
cp -f "$ROOT/apps/mobile/android/app/build/outputs/apk/debug/app-debug.apk" "$ROOT/dist-apps/Android/Bruno-AI-0.1.0-android.apk"

echo "✅ 전체 플랫폼 빌드 완료: $ROOT/dist-apps/"
ls -lh "$ROOT/dist-apps/"*
