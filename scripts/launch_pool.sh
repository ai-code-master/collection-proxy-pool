#!/bin/sh
set -eu

# Homebrew's Python launcher can detach the real interpreter from launchd.
# The pool only needs the standard library, so use macOS's direct interpreter.
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
