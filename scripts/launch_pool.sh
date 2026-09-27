#!/bin/sh
set -eu

# Prefer the project environment for optional Mihomo support. Fall back to
# macOS's direct interpreter because Homebrew launchers can detach under launchd.
unset __PYVENV_LAUNCHER__
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
PYTHON_BIN="$ROOT/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN=/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python
  if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN=/usr/bin/python3
  fi
fi
exec "$PYTHON_BIN" "$@"
