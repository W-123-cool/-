#!/bin/bash
# UVC �Կ�?? Web ��?��PC Camera A4 �� /dev/video2��
cd "$(dirname "$0")"
export VIDEO_SOURCE="${VIDEO_SOURCE:-/dev/video2}"
echo "=========================================="
echo "  YOLO11 �Կ�?? (UVC)"
echo "  ?��?: $VIDEO_SOURCE"
echo "  Web:    http://$(hostname -I | awk '{print $1}'):5000/"
echo "  ���:   Ctrl+C"
echo "=========================================="
python3 app.py
