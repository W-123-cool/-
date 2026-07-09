#!/bin/bash
# 多楼层智能导航 — 主启动（相机仅初始化；独立终端启动「仅深度」相机）

# ================= 配置区域 =================
WORKSPACE_DIR="$HOME/Desktop/rock_ws/ros_ws"
MICROROS_WS="$HOME/Desktop/rock_ws/microros_ws"
USB_SETUP_SCRIPT="$WORKSPACE_DIR/usb_auto_setup.sh"
SUDO_PASSWORD="rock"
SERIAL_PORT="/dev/rt_shell"
BAUD_RATE=1500000
MAP_PATH_1="$WORKSPACE_DIR/install/rt_robot_nav2/share/rt_robot_nav2/map/my_map6.yaml"

export ROS_DOMAIN_ID=0
CAMERA_DEVICE="/dev/video0"
SCRIPTS_DIR="$WORKSPACE_DIR/scripts"
ROS_ENV_SNIPPET="$SCRIPTS_DIR/ros_env.sh"
CAMERA_START_SCRIPT="$SCRIPTS_DIR/start_camera.sh"
# ===========================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

PIDS=()

cleanup() {
    echo -e "\n${YELLOW}[退出] 正在停止导航与传感器进程...${NC}"
    for pid in "${PIDS[@]}"; do
        kill -9 $pid >/dev/null 2>&1
    done
    pkill -f "ros2 launch" >/dev/null 2>&1
    pkill -f "v4l2_camera" >/dev/null 2>&1
    pkill -f "orbbec_camera" >/dev/null 2>&1
    pkill -f "depthimage_to_laserscan" >/dev/null 2>&1
    pkill -f "smart_switcher" >/dev/null 2>&1
    pkill -f "smart_building_navigator" >/dev/null 2>&1
    echo -e "${CYAN}[保留] MicroROS Agent 仍在运行（避免 MCU 底盘会话断开）${NC}"
    echo -e "${GREEN}[完成] 清理完毕。${NC}"
    exit 0
}

trap cleanup SIGINT

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  多楼层智能导航启动脚本${NC}"
echo -e "${GREEN}  (激光+IMU+深度辅助 Nav2)${NC}"
echo -e "${GREEN}========================================${NC}"

# ------------------------------------------------------------------------------
# 1. 编译与环境
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[1] 编译 ROS2 工作空间...${NC}"
cd "$WORKSPACE_DIR" || { echo -e "${RED}错误: 无法进入工作空间${NC}"; exit 1; }

colcon build
if [ $? -ne 0 ]; then
    echo -e "${YELLOW}警告: 编译存在非致命错误，尝试继续...${NC}"
fi

source /opt/ros/foxy/setup.bash
source install/setup.bash
if [ $? -ne 0 ]; then
    echo -e "${RED}错误: 环境加载失败${NC}"
    exit 1
fi
export ROS_DOMAIN_ID

chmod +x "$SCRIPTS_DIR"/*.sh 2>/dev/null
echo -e "${GREEN}编译与环境加载完成。${NC}"

# ------------------------------------------------------------------------------
# 2. USB + 深度相机设备初始化（不启动相机节点）
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[2] 配置 USB 与深度相机设备...${NC}"
if [ -f "$USB_SETUP_SCRIPT" ]; then
    echo "$SUDO_PASSWORD" | sudo -S bash "$USB_SETUP_SCRIPT" > /dev/null 2>&1
    echo "$SUDO_PASSWORD" | sudo -S udevadm control --reload-rules > /dev/null 2>&1
    echo "$SUDO_PASSWORD" | sudo -S udevadm trigger > /dev/null 2>&1
    echo -e "${GREEN}USB 配置完成。${NC}"
fi

if [ ! -f /etc/udev/rules.d/99-orbbec.rules ]; then
    echo "$SUDO_PASSWORD" | sudo -S tee /etc/udev/rules.d/99-orbbec.rules > /dev/null <<'UDEV_EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="2bc5", MODE="0666", GROUP="video"
KERNEL=="video*", ATTRS{idVendor}=="2bc5", MODE="0666", GROUP="video"
UDEV_EOF
    echo "$SUDO_PASSWORD" | sudo -S udevadm control --reload-rules > /dev/null 2>&1
    echo "$SUDO_PASSWORD" | sudo -S udevadm trigger > /dev/null 2>&1
fi

for dev in /dev/video0 /dev/video1; do
    [ -e "$dev" ] && echo "$SUDO_PASSWORD" | sudo -S chmod a+rw "$dev" 2>/dev/null
done

pkill -f "orbbec_camera" 2>/dev/null
pkill -f "v4l2_camera" 2>/dev/null

if lsusb | grep -q "2bc5:0403"; then
    echo -e "${GREEN}Orbbec Astra Pro (2bc5:0403) 已识别${NC}"
else
    echo -e "${RED}警告: 未检测到 Orbbec 深度相机 USB${NC}"
fi

echo -e "${CYAN}----------------------------------------------${NC}"
echo -e "${CYAN}  仅深度模式（不启用彩色 / v4l2）${NC}"
echo -e "${CYAN}  Nav2 融合: /scan + /scan_depth${NC}"
echo -e "${CYAN}  独立终端启动深度相机:${NC}"
echo -e "${CYAN}    bash $CAMERA_START_SCRIPT${NC}"
echo -e "${CYAN}----------------------------------------------${NC}"

# ------------------------------------------------------------------------------
# 3. 启动传感器 (IMU & Lidar)
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[3] 启动传感器 (IMU & Lidar)...${NC}"
ros2 launch dm_imu dm_imu_rviz.launch.py &
PIDS+=($!)
sleep 1
ros2 launch lslidar_driver lsn10p_launch.py &
PIDS+=($!)
sleep 2
echo -e "${GREEN}传感器已启动。${NC}"

# ------------------------------------------------------------------------------
# 4. MicroROS Agent
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[4] 启动 MicroROS Agent...${NC}"
cd "$MICROROS_WS" || { echo -e "${RED}错误: 无法进入 Microros 空间${NC}"; exit 1; }
source /opt/ros/foxy/setup.bash
source install/setup.bash
ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888 &
AGENT_PID=$!
disown "${AGENT_PID}" 2>/dev/null || true
sleep 2
echo -e "${GREEN}Agent 已启动 (PID ${AGENT_PID}，退出本脚本不会停止 Agent)。${NC}"

cd "$WORKSPACE_DIR"
source /opt/ros/foxy/setup.bash
source install/setup.bash

# ------------------------------------------------------------------------------
# 5. 底盘 + 相机终端提示
# ------------------------------------------------------------------------------
echo -e "\n${BLUE}==================================================${NC}"
echo -e "${BLUE}  【终端2】底盘 minicom:${NC}"
echo -e "${BLUE}  minicom -D $SERIAL_PORT -b $BAUD_RATE${NC}"
echo -e "${BLUE}  microros_chassis udp 10.10.10.31 8888${NC}"
echo -e "${BLUE}  chassis_car_app${NC}"
echo -e "${BLUE}--------------------------------------------------${NC}"
echo -e "${BLUE}  【终端3】深度相机-仅深度 (Nav2 前/后启动均可):${NC}"
echo -e "${BLUE}  bash $CAMERA_START_SCRIPT${NC}"
echo -e "${BLUE}--------------------------------------------------${NC}"
echo -e "${BLUE}  【终端4 可选】检查深度融合:${NC}"
echo -e "${BLUE}  source $ROS_ENV_SNIPPET${NC}"
echo -e "${BLUE}  ros2 topic hz /camera/depth/image_raw${NC}"
echo -e "${BLUE}  ros2 topic hz /scan_depth${NC}"
echo -e "${BLUE}==================================================${NC}"
read -p "按 Enter 键确认底盘已就绪..."

# ------------------------------------------------------------------------------
# 6. 选择控制模式
# ------------------------------------------------------------------------------
echo -e "\n${CYAN}请选择控制模式:${NC}"
echo -e "  1. ${GREEN}图形化模式${NC} (启动 RViz, 鼠标点击)"
echo -e "  2. ${YELLOW}命令行模式${NC} (终端输入 x y yaw)"
echo -e "  3. ${BLUE}MQTT 房间导航模式${NC} (发送房间号，自动跨楼层)"
read -p "请输入选项 (1/2/3, 默认 1): " mode_choice

# ------------------------------------------------------------------------------
# 7. 启动 Nav2（含深度辅助 depth_nav_assist）+ 智能导航
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[启动] 导航系统 (激光+IMU+深度融合) ...${NC}"

if [ ! -f "$MAP_PATH_1" ]; then
    echo -e "${RED}错误: 未找到初始地图 $MAP_PATH_1${NC}"
    exit 1
fi

RVIZ_OPT="true"
[[ "$mode_choice" != "1" ]] && RVIZ_OPT="false"

ros2 launch rt_robot_nav2 rt_robot_nav2_complete.launch.py \
    use_slam:=false \
    use_nav:=true \
    use_depth_nav:=true \
    map_file:=$MAP_PATH_1 \
    open_rviz:=$RVIZ_OPT &
NAV_PID=$!
PIDS+=($NAV_PID)
sleep 5

SMART_SWITCHER_LOG_SCRIPT="${WORKSPACE_DIR}/scripts/smart_switcher_log.sh"
if [[ -f "${SMART_SWITCHER_LOG_SCRIPT}" ]]; then
    # shellcheck source=/dev/null
    source "${SMART_SWITCHER_LOG_SCRIPT}"
    SWITCHER_PID="$(ai_car_start_smart_switcher "${WORKSPACE_DIR}")"
else
    ros2 run smart_nav_manager smart_switcher &
    SWITCHER_PID=$!
fi
PIDS+=($SWITCHER_PID)

echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN}✅ 系统就绪！深度辅助: use_depth_nav:=true${NC}"
echo -e "${GREEN}   /scan=激光  /scan_depth=深度(需终端3)${NC}"

if [ "$mode_choice" == "3" ]; then
    echo -e "${YELLOW}📱 MQTT 房间导航模式已激活${NC}"
    echo -e "${CYAN}---------------------------------------------${NC}"
    echo -e "${GREEN}1. 发送房间号: robot/nav_room${NC}"
    echo -e "${GREEN}2. 状态: robot/status${NC}"
    echo -e "${GREEN}3. 电梯: elevator/response${NC}"
    echo -e "${CYAN}---------------------------------------------${NC}"
elif [ "$mode_choice" == "2" ]; then
    echo -e "${YELLOW}⌨️  命令行交互模式已激活${NC}"
    echo -e "  格式: x y yaw (弧度). 输入 'q' 退出"
    while true; do
        echo -ne "${GREEN}>> Goal (x y yaw): ${NC}"
        read input
        if [[ "$input" == "q" || "$input" == "quit" ]]; then
            cleanup
        fi
        read x y yaw <<< $(echo $input | tr -s ' ')
        if [[ -z "$x" || -z "$y" ]]; then
            echo -e "${RED}格式错误${NC}"
            continue
        fi
        yaw=${yaw:-0.0}
        qz=$(awk "BEGIN {print sin($yaw/2)}")
        qw=$(awk "BEGIN {print cos($yaw/2)}")
        ros2 action send_goal /navigate_to_pose nav2_msgs/action/NavigateToPose "{
            pose: {
                header: {frame_id: 'map'},
                pose: {
                    position: {x: $x, y: $y, z: 0.0},
                    orientation: {x: 0.0, y: 0.0, z: $qz, w: $qw}
                }
            }
        }" --feedback > /dev/null 2>&1
        echo -e "${GREEN}指令已发送${NC}"
    done
else
    echo -e "${YELLOW}🖱️  请在 RViz 中使用 'Nav2 Goal' 插件设置目标${NC}"
fi

echo -e "${YELLOW}💡 Ctrl+C 停止导航/传感器（Agent 保持运行；相机关闭请在相机终端 Ctrl+C）${NC}"
echo -e "${GREEN}=============================================${NC}"

wait $NAV_PID
