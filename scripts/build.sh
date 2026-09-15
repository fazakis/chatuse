#!/bin/sh
set -eu
case "$(uname -s)" in
  Linux) exec sh "$(dirname -- "$0")/build-linux.sh" ;;
  Darwin) ;;
  *) printf '%s\n' 'Chatuse supports macOS and Linux/X11.' >&2; exit 1 ;;
esac
chatuse_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$chatuse_root"
swift build -c release
chatuse_bin=$(swift build -c release --show-bin-path)
chatuse_app="$chatuse_root/runtime/Chatuse.app"
mkdir -p "$chatuse_app/Contents/MacOS" "$chatuse_app/Contents/Resources" "$chatuse_root/artifacts"
cp "$chatuse_bin/chatuse-native" "$chatuse_app/Contents/MacOS/Chatuse"
cat > "$chatuse_app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.chatuse.helper</string>
<key>CFBundleName</key><string>Chatuse</string>
<key>CFBundleDisplayName</key><string>Chatuse</string>
<key>CFBundleExecutable</key><string>Chatuse</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.1.0</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>LSUIElement</key><true/>
<key>NSHighResolutionCapable</key><true/>
<key>NSScreenCaptureUsageDescription</key><string>Chatuse captures your screen for computer-use tasks you request.</string>
</dict></plist>
PLIST
codesign --force --sign "${CHATUSE_SIGN_IDENTITY:--}" --identifier local.chatuse.helper "$chatuse_app"
ln -sf "$(command -v node)" "$chatuse_root/runtime/node"
chmod +x chatuse scripts/*.sh
printf 'Built %s\n' "$chatuse_app"
sh "$chatuse_root/scripts/build-pointer.sh"
