#!/bin/bash
# Ctrl+C ��?�ҹ������ŏ��?��?����? MicroROS Agent / �ԏ�� minicom��
#
# ��ˡ:
#   bash ~/Desktop/restart_nav_only.sh          # ? RViz
#   bash ~/Desktop/restart_nav_only.sh --no-rviz

WORKSPACE_DIR="${WORKSPACE_DIR:-$HOME/Desktop/rock_ws/ros_ws}"
MAP_PATH="${MAP_PATH:-$WORKSPACE_DIR/install/rt_robot_nav2/share/rt_robot_nav2/map/my_map6.yaml}"
OPEN_RVIZ="true"
[[ "${1:-}" == "--no-rviz" ]] && OPEN_RVIZ="false"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  ?�ŏ��?�ҡ���α Agent / ��?��${NC}"
echo -e "${GREEN}========================================${NC}"

if ! pgrep -f "micro_ros_agent.*8888" >/dev/null 2>&1; then
  echo -e "${RED}[�ٹ�] MicroROS Agent ̤�όԡ�?�菴�? Agent ��?����?${NC}"
fi

echo -e "${YELLOW}[1] ������α?��?����${NC}"
pkill -f "rt_robot_nav2_complete.launch.py" 2>/dev/null || true
pkill -f "smart_switcher" 2>/dev/null || true
pkill -f "smart_building_navigator" 2>/dev/null || true
pkill -f "nav2_" 2>/dev/null || true
pkill -f "amcl" 2>/dev/null || true
pkill -f "map_server" 2>/dev/null || true
pkill -f "auto_initialpose" 2>/dev/null || true
sleep 2

cd "$WORKSPACE_DIR" || { echo -e "${RED}��ˡ?�� ${WORKSPACE_DIR}${NC}"; exit 1; }
set +u
source /opt/ros/foxy/setup.bash 2>/dev/null || source /opt/ros/humble/setup.bash 2>/dev/null || true
source install/setup.bash
set -u
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"

if [[ ! -f "$MAP_PATH" ]]; then
  echo -e "${RED}[??] ��?��¸��: ${MAP_PATH}${NC}"
  exit 1
fi

echo -e "${YELLOW}[2] ???���� (IMU / ���)��${NC}"
if ! pgrep -f "dm_imu_rviz.launch.py" >/dev/null 2>&1; then
  echo "  ���? IMU��"
  ros2 launch dm_imu dm_imu_rviz.launch.py &
  sleep 1
fi
if ! pgrep -f "lsn10p_launch.py" >/dev/null 2>&1; then
  echo "  ���?��ᶡ�"
  ros2 launch lslidar_driver lsn10p_launch.py &
  sleep 2
fi

echo -e "${YELLOW}[3] ���? Nav2 (open_rviz=${OPEN_RVIZ})��${NC}"
ros2 launch rt_robot_nav2 rt_robot_nav2_complete.launch.py \
  use_slam:=false \
  use_nav:=true \
  map_file:="$MAP_PATH" \
  open_rviz:="$OPEN_RVIZ" &
NAV_PID=$!
sleep 8

echo -e "${YELLOW}[4] ���? smart_switcher��${NC}"
ros2 run smart_nav_manager smart_switcher &
SW_PID=$!

echo -e "${YELLOW}[5] ??��ϰ̻� (���� RViz: Fixed Frame [map] does not exist)��${NC}"
if grep -qE '^(rt_robot_initial_pose|initial_pose):' "$MAP_PATH" 2>/dev/null; then
  ros2 run rt_robot_nav2 auto_initialpose.py \
    --ros-args -p "map_yaml:=${MAP_PATH}" -p publish_delay_sec:=3.0 &
  sleep 5
else
  echo -e "${RED}  ��? yaml Τ��ͭ initial_pose��?�� RViz ����2D Pose Estimate��${NC}"
fi

echo ""
echo -e "${GREEN}? ?��?��ŏ��${NC}"
echo -e "${CYAN}  �� RViz л����?���� 5~10 �ù�??:${NC}"
echo -e "${CYAN}    ros2 topic hz /map${NC}"
echo -e "${CYAN}    ros2 run tf2_ros tf2_echo map odom${NC}"
echo -e "${CYAN}  �� RViz ����? �� 2D Pose Estimate ���첼����Ͱ���${NC}"
echo -e "${YELLOW}Ctrl+C ����ܵ��ܡ��Բ��� Agent��${NC}"
echo ""

wait "$NAV_PID" 2>/dev/null || wait "$SW_PID" 2>/dev/null || true
