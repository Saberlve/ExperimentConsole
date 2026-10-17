#!/usr/bin/env bash
# All camera service source belongs to experiment_console; SDKs come from Python.
set -euo pipefail
console_root="$(cd -- "$(dirname -- "$0")" && pwd)"
cd -- "$console_root"
camera_python="${CAMERA_PYTHON:-${CONSOLE_PYTHON:-python3}}"
exec "$camera_python" -m backend.camera_server "$@" \
  --output-dir "${CAMERA_OUTPUT_DIR:-$console_root/WM_fisrtFrame}"
