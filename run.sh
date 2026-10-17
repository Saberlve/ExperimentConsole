#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
exec "${CONSOLE_PYTHON:-python3}" -m backend.server "$@"
