#!/bin/sh
# Install Noryx in a project-owned virtual environment and expose both commands.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
NORYX_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
VENV_DIR=${NORYX_VENV_DIR:-"$NORYX_ROOT/.venv"}
USER_BIN=${NORYX_USER_BIN:-"$HOME/.local/bin"}

if [ ! -x "$VENV_DIR/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade "$NORYX_ROOT"
mkdir -p "$USER_BIN"
ln -sfn "$VENV_DIR/bin/noryx" "$USER_BIN/noryx"
ln -sfn "$VENV_DIR/bin/nexus" "$USER_BIN/nexus"

"$USER_BIN/noryx" --version
echo "Installed Noryx at $USER_BIN/noryx"
echo "Legacy compatibility command: $USER_BIN/nexus"
