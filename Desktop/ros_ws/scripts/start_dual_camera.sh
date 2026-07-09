#!/bin/bash
# ��������ԡ�UVC ����? + Orbbec ������ Nav2 ���
#
# ?ü 1���ܵ�������뿼�١���??���??ü��:
#   bash start_dual_camera.sh
#
# ?ü 2��UVC �Կ�?? Web��:
#   cd ~/Desktop/yolo11 && python3 app.py
#
# ?ü 3��?��?���� use_depth_nav��:
#   bash start_nav_stack_light.sh  �� start_multi_map.sh
#
export KEEP_UVC=1
exec "$(dirname "$0")/start_camera.sh" "$@"
