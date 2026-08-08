#!/bin/sh
# Compatibility wrapper for the canonical Noryx installer.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$SCRIPT_DIR/install_noryx_command.sh" "$@"
