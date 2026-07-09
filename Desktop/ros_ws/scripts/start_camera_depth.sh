#!/bin/bash
# 兼容旧命令名，实际调用 start_camera.sh（仅深度）
exec "$(dirname "$0")/start_camera.sh" "$@"
