#!/bin/sh
# Install the Nexus launcher into the user's PATH without modifying the
# externally-managed Python installation.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
NEXUS_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
LAUNCHER="$NEXUS_ROOT/bin/nexus"
VENV_DIR="$NEXUS_ROOT/.venv"
USER_BIN="$HOME/.local/bin"
TARGET="$USER_BIN/nexus"

if [ ! -x "$LAUNCHER" ]; then
    echo "Nexus launcher is missing or not executable: $LAUNCHER" >&2
    exit 1
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade "$NEXUS_ROOT"

mkdir -p "$USER_BIN"
ln -sfn "$LAUNCHER" "$TARGET"

case ":$PATH:" in
    *":$USER_BIN:"*) ;;
    *)
        echo "Installed $TARGET, but $USER_BIN is not on PATH." >&2
        echo "Add this to your shell profile, then open a new terminal:" >&2
        echo "  export PATH=\"$USER_BIN:\$PATH\"" >&2
        exit 1
        ;;
esac

echo "Installed Nexus: $TARGET"
echo "Run: nexus --help"
