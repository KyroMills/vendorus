#!/bin/bash
# Double-click to open Vendorus in its own window (macOS).
# Closing the Vendorus window stops it. For a Dock app, run scripts/make_mac_app.sh.
cd "$(dirname "$0")" || exit 1

# Finder and the Dock app start with a minimal PATH; add the usual Python homes.
export PATH="/Library/Frameworks/Python.framework/Versions/Current/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  for candidate in python3.14 python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  done
fi

if [ -z "$PY" ]; then
  echo "Vendorus needs Python 3.12 or newer. Install it from https://www.python.org/downloads/"
  [ -t 0 ] && read -n 1 -s -r -p "Press any key to close"
  exit 1
fi

"$PY" launcher.py "$@"
status=$?
if [ $status -ne 0 ] && [ -t 0 ]; then
  echo
  read -n 1 -s -r -p "Press any key to close"
fi
exit $status
