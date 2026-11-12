#!/bin/bash
# Build ~/Applications/JARVIS.app pointing at this folder. Re-run after moving JARVIS.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/JARVIS.app"
rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
# icon
SET="$(mktemp -d)/JARVIS.iconset"; mkdir -p "$SET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$ROOT/assets/jarvis-icon.png" --out "$SET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$ROOT/assets/jarvis-icon.png" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$SET" -o "$APP/Contents/Resources/JARVIS.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>JARVIS</string>
  <key>CFBundleDisplayName</key><string>JARVIS</string>
  <key>CFBundleIdentifier</key><string>local.jarvis.app</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>JARVIS</string>
  <key>CFBundleIconFile</key><string>JARVIS</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>JARVIS listens for "Hey JARVIS" and your questions.</string>
</dict></plist>
PLIST
# a real compiled launcher (not a script that execs Python), so macOS shows the
# microphone prompt for JARVIS and lists JARVIS in Privacy & Security
clang -O2 -o "$APP/Contents/MacOS/JARVIS" -DJARVIS_ROOT="\"$ROOT\"" "$ROOT/assets/launcher.c"
codesign --force --deep --sign - --identifier local.jarvis.app "$APP"
touch "$APP"
echo "Built $APP"
