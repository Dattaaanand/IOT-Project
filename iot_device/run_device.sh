#!/bin/sh
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
    python3 iot_device.py "$@"
else
    python iot_device.py "$@"
fi
