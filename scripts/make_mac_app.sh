#!/bin/bash
# Build Vendorus.app: a real Mac app with its own window, Dock icon and menu.
# Opening it starts the dashboard inside the app window; quitting (Cmd+Q or
# closing the window) stops it.
#
#   scripts/make_mac_app.sh            installs to ~/Applications
#   scripts/make_mac_app.sh /some/dir  installs somewhere else
#
# The app remembers where this project folder is. If you move the folder,
# run this script again.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST_DIR="${1:-$HOME/Applications}"
APP="$DEST_DIR/Vendorus.app"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 1. Make sure the project's Python environment exists.
if [ ! -x "$REPO/.venv/bin/python" ]; then
  echo "Setting up the Python environment first..."
  "$REPO/Vendorus.command" --setup-only
fi
PY="$REPO/.venv/bin/python"

# 2. Find the GUI Python executable behind the venv. Copying it into the app
#    makes macOS treat the window as "Vendorus" (name, icon, Dock) rather
#    than "Python".
GUI_PYTHON="$("$PY" -c 'import os, sys; p = os.path.join(sys.base_prefix, "Resources/Python.app/Contents/MacOS/Python"); print(p if os.path.exists(p) else "")')"

mkdir -p "$DEST_DIR"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

if [ -n "$GUI_PYTHON" ]; then
  cp "$GUI_PYTHON" "$APP/Contents/MacOS/vendorus-python"
  RUNNER='"$HERE/vendorus-python"'
else
  echo "Note: this Python isn't a framework build; the Dock may show Python's icon."
  RUNNER="\"$PY\""
fi

# 3. The app's entry point: start the launcher in app-window mode.
cat > "$APP/Contents/MacOS/Vendorus" <<SCRIPT
#!/bin/bash
HERE="\$(cd "\$(dirname "\$0")" && pwd)"
REPO="$REPO"
if [ ! -f "\$REPO/launcher.py" ]; then
  osascript -e 'display alert "Vendorus can'"'"'t find its project folder" message "Expected it at $REPO. If you moved it, run scripts/make_mac_app.sh again." as critical'
  exit 1
fi
cd "\$REPO"
exec $RUNNER "\$REPO/launcher.py"
SCRIPT
chmod +x "$APP/Contents/MacOS/Vendorus"

# 4. Icon, drawn with Pillow (installed with Streamlit).
"$PY" "$REPO/scripts/make_icon.py" "$WORK/icon.png"
ICONSET="$WORK/Vendorus.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size "$WORK/icon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z $double $double "$WORK/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Vendorus.icns"

# 5. Identity shown by macOS.
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Vendorus</string>
  <key>CFBundleDisplayName</key><string>Vendorus</string>
  <key>CFBundleIdentifier</key><string>local.vendorus.app</string>
  <key>CFBundleExecutable</key><string>Vendorus</string>
  <key>CFBundleIconFile</key><string>Vendorus</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.business</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
</dict>
</plist>
PLIST

# 6. Sign locally (ad hoc) so macOS runs the copied Python inside the app.
codesign --force --deep --sign - "$APP" 2>/dev/null
touch "$APP"
# Refresh the icon in Finder and the Dock.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true

echo "Built $APP"
echo "Open it from Finder (Go > Home > Applications), then right-click its Dock icon"
echo "and choose Options > Keep in Dock."
