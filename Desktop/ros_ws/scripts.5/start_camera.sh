#!/bin/bash
# Astra Pro 仅深度模式（导航用，不启用彩色）
# 发布 /camera/depth/image_raw → Nav2 融合为 /scan_depth
export ROS_DOMAIN_ID=0

source /opt/ros/foxy/setup.bash
source "$HOME/orbbec_ws/install/setup.bash" 2>/dev/null || {
  echo "[错误] 未找到 ~/orbbec_ws/install/setup.bash，请先在板子上编译 OrbbecSDK_ROS2"
  exit 1
}

pkill -f "orbbec_camera" 2>/dev/null
pkill -f "v4l2_camera" 2>/dev/null
sleep 1

# 双相机模式：KEEP_UVC=1 时不卸载 uvcvideo（独立 UVC 做人检 + Orbbec 做深度避障）
# 仅深度单相机且 Orbbec 启动失败时，可: KEEP_UVC=0 bash start_camera.sh
if [ "${KEEP_UVC:-1}" = "1" ]; then
  echo "[提示] KEEP_UVC=1：保留 uvcvideo，可与 UVC 摄像头（如 /dev/video2）并行"
else
  if lsmod | grep -q uvcvideo; then
    echo "[提示] 正在卸载 uvcvideo（仅深度模式，不需要彩色 UVC）..."
    sudo modprobe -r uvcvideo 2>/dev/null || echo "[警告] 无法卸载 uvcvideo，若启动失败请手动: sudo modprobe -r uvcvideo"
  fi
fi

echo "=========================================="
echo "  Astra Pro — 仅深度模式（无彩色）"
echo "  深度话题: /camera/depth/image_raw"
echo "  导航融合: /scan_depth（需 Nav2 use_depth_nav:=true）"
echo "  并行 UVC: KEEP_UVC=1（默认）+ yolo11 使用 /dev/video2"
echo "  停止: Ctrl+C"
echo "=========================================="

ros2 launch orbbec_camera astra.launch.py \
  product_id:=0x0403 \
  enable_color:=false \
  enable_depth:=true \
  depth_fps:=30 \
  depth_width:=640 \
  depth_height:=480 \
  enable_ir:=false \
  connection_delay:=500
