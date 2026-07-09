#!/bin/bash
# ?�� Orbbec ���١����� rqt_image_view �� 16UC1��
source /opt/ros/foxy/setup.bash
source "$HOME/orbbec_ws/install/setup.bash" 2>/dev/null || true
TOPIC="${1:-/camera/depth/image_raw}"
python3 "$(dirname "$0")/view_depth.py" "$TOPIC"
