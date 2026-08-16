#!/bin/bash
# Finder/Dock에서 누를 수 있는 "AI Daily Briefing.app"을 만든다.
#
# 이 앱은 서버를 켜고 브라우저를 연다. 예전 버전은 `open http://localhost:8787/`
# 한 줄이라 서버가 내려가 있으면 빈 화면만 떴는데, 아이콘이 앱처럼 생겨서 "서버가
# 안 켜진다"고 오해하기 딱 좋았음.
#
# 사용법: ./make_app.sh   (기존 앱이 있으면 덮어씀)
set -euo pipefail

APP="/Applications/AI Daily Briefing.app"
LABEL="com.jesper.ai-daily-briefing"
PORT=8787
HERE="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>${LABEL}-launcher</string>
  <key>CFBundleName</key><string>AI Daily Briefing</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.1</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/launcher" <<LAUNCHER
#!/bin/bash
# 서버가 살아있으면 브라우저만 열고, 죽어있으면 먼저 살린다.
PORT=${PORT}
LABEL=${LABEL}
REPO="${HERE}"

up() { curl -s -o /dev/null --max-time 1 "http://localhost:\$PORT/"; }

if ! up; then
  # 평소 경로: launchd에 등록된 메뉴바 앱을 깨운다(로그인 시 자동 실행되는 그것).
  launchctl kickstart "gui/\$(id -u)/\$LABEL" 2>/dev/null ||
    # 등록이 안 돼 있으면(bootout 했거나 새 머신) 직접 띄운다.
    ("\$REPO/.venv/bin/python3" "\$REPO/menubar_app.py" >> "\$REPO/menubar.log" 2>&1 &)

  # 첫 실행은 소켓 바인드까지 1~2초 걸림. 떴는지 확인하고 나서 브라우저를 연다.
  for _ in \$(seq 1 20); do up && break; sleep 0.5; done
fi

open "http://localhost:\$PORT/"
LAUNCHER
chmod +x "$APP/Contents/MacOS/launcher"

# 아이콘(AppIcon.icns)은 이미 있으면 그대로 둔다. menubar_icon.png는 메뉴바용 흑백
# 템플릿이라 앱 아이콘으로 쓰면 새까만 덩어리가 됨.
[ -f "$APP/Contents/Resources/AppIcon.icns" ] || echo "  (아이콘 없음 - Finder 기본 아이콘으로 표시됩니다)"

touch "$APP"  # Finder가 아이콘/버전 캐시를 다시 읽게
echo "만들었어요: $APP"
