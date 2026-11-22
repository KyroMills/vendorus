#!/bin/bash
# Build Vendorus.app: a Dock app that starts the dashboard and opens it in
# your browser. Clicking it again reopens the browser; Quit stops the
# dashboard.
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

escaped_repo=${REPO//\\/\\\\}
escaped_repo=${escaped_repo//\"/\\\"}

cat > "$WORK/Vendorus.applescript" <<APPLESCRIPT
property repo : "$escaped_repo"

on run
	startDashboard()
end run

on reopen
	startDashboard()
end reopen

on startDashboard()
	try
		do shell script "cd " & quoted form of repo & " && ./Vendorus.command --background"
	on error errorMessage
		display dialog "Vendorus couldn't start." & return & return & errorMessage ¬
			buttons {"OK"} default button 1 with icon caution with title "Vendorus"
	end try
end startDashboard

on quit
	try
		do shell script "cd " & quoted form of repo & " && ./Vendorus.command --stop"
	end try
	continue quit
end quit
APPLESCRIPT

mkdir -p "$DEST_DIR"
rm -rf "$APP"
# -s: stay open, so the app shows in the Dock while the dashboard runs.
osacompile -s -o "$APP" "$WORK/Vendorus.applescript"

# Icon: draw it with Pillow (installed with Streamlit), then build an .icns file.
PY="$REPO/.venv/bin/python"
if [ -x "$PY" ] && "$PY" "$REPO/scripts/make_icon.py" "$WORK/icon.png" 2>/dev/null; then
  ICONSET="$WORK/Vendorus.iconset"
  mkdir -p "$ICONSET"
  for size in 16 32 128 256 512; do
    sips -z $size $size "$WORK/icon.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z $double $double "$WORK/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/applet.icns"
  # Newer macOS reads the compiled asset catalog first; remove it so the new icon shows.
  rm -f "$APP/Contents/Resources/Assets.car"
  /usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$APP/Contents/Info.plist" 2>/dev/null || true
else
  echo "Note: couldn't draw the icon (run Vendorus.command once first); using the default icon."
fi

# Name and identity shown by macOS.
/usr/libexec/PlistBuddy -c "Set :CFBundleName Vendorus" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string local.vendorus.launcher" \
  "$APP/Contents/Info.plist" 2>/dev/null ||
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier local.vendorus.launcher" "$APP/Contents/Info.plist"
# Re-sign locally (ad hoc) because the icon and Info.plist changed after osacompile signed it.
codesign --force --deep --sign - "$APP" 2>/dev/null || true
touch "$APP"

echo "Built $APP"
echo "Open it from Finder (Go > Home > Applications), then right-click its Dock icon"
echo "and choose Options > Keep in Dock."
