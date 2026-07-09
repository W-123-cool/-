#!/bin/bash
# 注意：已移除 set -e，确保脚本稳健运行，仅在 Ctrl+C 时退出

# ================= 配置区域 =================
WORKSPACE_DIR="$HOME/Desktop/rock_ws/ros_ws"
MICROROS_WS="$HOME/Desktop/rock_ws/microros_ws"
USB_SETUP_SCRIPT="$HOME/Desktop/rock_ws/ros_ws/usb_auto_setup.sh"
SUDO_PASSWORD="rock"
SERIAL_PORT="/dev/rt_shell"
BAUD_RATE=1500000

# 地图路径 (仅用于初始加载，后续由 Python 节点动态切换)
MAP_PATH_1="$WORKSPACE_DIR/install/rt_robot_nav2/share/rt_robot_nav2/map/my_map6.yaml"

# ---------- 相机 (仅初始化，不在此脚本启动) ----------
export ROS_DOMAIN_ID=0
CAMERA_DEVICE="/dev/video0"
ROS_ENV_SNIPPET="$HOME/Desktop/rock_ws/ros_ws/scripts/ros_env.sh"
CAMERA_START_SCRIPT="$HOME/Desktop/rock_ws/ros_ws/scripts/start_camera.sh"
# ===========================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

PIDS=()

# 清理函数（不启动相机；不杀 MicroROS Agent / minicom）
cleanup() {
    echo -e "\n${YELLOW}[退出] 正在停止导航与传感器进程...${NC}"
    for pid in "${PIDS[@]}"; do
        kill -9 $pid >/dev/null 2>&1
    done
    pkill -f "ros2 launch" >/dev/null 2>&1
    pkill -f "v4l2_camera" >/dev/null 2>&1
    pkill -f "orbbec_camera" >/dev/null 2>&1
    pkill -f "smart_switcher" >/dev/null 2>&1
    pkill -f "smart_building_navigator" >/dev/null 2>&1
    echo -e "${CYAN}[保留] MicroROS Agent 仍在运行（避免 MCU 底盘会话断开）${NC}"
    echo -e "${GREEN}[完成] 清理完毕。${NC}"
    exit 0
}

trap cleanup SIGINT

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  多楼层智能导航启动脚本${NC}"
echo -e "${GREEN}========================================${NC}"

# ------------------------------------------------------------------------------
# 1. 编译与环境
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[1] 编译 ROS2 工作空间...${NC}"
cd "$WORKSPACE_DIR" || { echo -e "${RED}错误: 无法进入工作空间${NC}"; exit 1; }

colcon build --packages-ignore mqtt_nav_bridge
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

# 生成：其它终端 ROS 环境 + 相机启动脚本
mkdir -p "$(dirname "$ROS_ENV_SNIPPET")"
cat > "$ROS_ENV_SNIPPET" <<EOF
#!/bin/bash
export ROS_DOMAIN_ID=$ROS_DOMAIN_ID
source /opt/ros/foxy/setup.bash
source $WORKSPACE_DIR/install/setup.bash
echo "ROS 环境已加载 (DOMAIN_ID=\$ROS_DOMAIN_ID)"
EOF
chmod +x "$ROS_ENV_SNIPPET"

cat > "$CAMERA_START_SCRIPT" <<'EOF'
#!/bin/bash
# 在单独终端运行此脚本启动 Astra Pro 彩色相机
export ROS_DOMAIN_ID=0
source /opt/ros/foxy/setup.bash
source "$HOME/Desktop/rock_ws/ros_ws/install/setup.bash"

CAMERA_DEVICE="/dev/video0"

if [ ! -e "$CAMERA_DEVICE" ]; then
    echo "[错误] 未找到 $CAMERA_DEVICE，请先运行主启动脚本完成 USB 初始化"
    exit 1
fi

pkill -f "orbbec_camera" 2>/dev/null
pkill -f "v4l2_camera" 2>/dev/null
sleep 1

echo "=========================================="
echo "  启动 Astra Pro 彩色相机 (v4l2)"
echo "  设备: $CAMERA_DEVICE"
echo "  Topic: /image_raw"
echo "  停止: Ctrl+C"
echo "=========================================="

ros2 run v4l2_camera v4l2_camera_node --ros-args \
    -p video_device:="$CAMERA_DEVICE" \
    -p image_size:="[640,480]" \
    -p time_per_frame:="[1,15]"
EOF
chmod +x "$CAMERA_START_SCRIPT"

echo -e "${GREEN}编译与环境加载完成。${NC}"

# ------------------------------------------------------------------------------
# 2. USB 配置 + 相机初始化（不启动节点）
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[2] 配置 USB 与相机设备...${NC}"
if [ -f "$USB_SETUP_SCRIPT" ]; then
    echo "$SUDO_PASSWORD" | sudo -S bash "$USB_SETUP_SCRIPT" > /dev/null 2>&1
    echo "$SUDO_PASSWORD" | sudo -S udevadm control --reload-rules > /dev/null 2>&1
    echo "$SUDO_PASSWORD" | sudo -S udevadm trigger > /dev/null 2>&1
    echo -e "${GREEN}USB 配置完成。${NC}"
fi

# Orbbec Astra Pro udev
if [ ! -f /etc/udev/rules.d/99-orbbec.rules ]; then
    echo "$SUDO_PASSWORD" | sudo -S tee /etc/udev/rules.d/99-orbbec.rules > /dev/null <<'UDEV_EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="2bc5", MODE="0666", GROUP="video"
KERNEL=="video*", ATTRS{idVendor}=="2bc5", MODE="0666", GROUP="video"
UDEV_EOF
    echo "$SUDO_PASSWORD" | sudo -S udevadm control --reload-rules > /dev/null 2>&1
    echo "$SUDO_PASSWORD" | sudo -S udevadm trigger > /dev/null 2>&1
fi

for dev in /dev/video0 /dev/video1; do
    if [ -e "$dev" ]; then
        echo "$SUDO_PASSWORD" | sudo -S chmod a+rw "$dev" 2>/dev/null
    fi
done

if [ -e "$CAMERA_DEVICE" ]; then
    echo -e "${GREEN}相机设备已就绪: $CAMERA_DEVICE${NC}"
    if command -v v4l2-ctl >/dev/null 2>&1; then
        v4l2-ctl --device="$CAMERA_DEVICE" --info 2>/dev/null | head -3
    fi
    echo -e "${CYAN}----------------------------------------------${NC}"
    echo -e "${CYAN}  相机未在本脚本启动（避免卡顿）${NC}"
    echo -e "${CYAN}  请在新终端执行以下任一命令启动相机:${NC}"
    echo -e "${CYAN}    bash $CAMERA_START_SCRIPT${NC}"
    echo -e "${CYAN}  或:${NC}"
    echo -e "${CYAN}    source $ROS_ENV_SNIPPET && bash $CAMERA_START_SCRIPT${NC}"
    echo -e "${CYAN}----------------------------------------------${NC}"
else
    echo -e "${RED}警告: 未找到 $CAMERA_DEVICE，请检查相机 USB 连接${NC}"
fi

# ------------------------------------------------------------------------------
# 3. 启动传感器 (仅 IMU & Lidar)
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
# 5. 底盘连接提示
# ------------------------------------------------------------------------------
echo -e "\n${BLUE}==================================================${NC}"
echo -e "${BLUE}  【终端2】连接底盘:${NC}"
echo -e "${BLUE}  minicom -D $SERIAL_PORT -b $BAUD_RATE${NC}"
echo -e "${BLUE}  输入: microros_chassis udp 10.10.10.31 8888${NC}"
echo -e "${BLUE}  输入: chassis_car_app${NC}"
echo -e "${BLUE}--------------------------------------------------${NC}"
echo -e "${BLUE}  【终端3】启动相机:${NC}"
echo -e "${BLUE}  bash $CAMERA_START_SCRIPT${NC}"
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
# 7. 启动 Nav2 和 智能导航节点
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[启动] 导航系统与多楼层管理器...${NC}"

if [ ! -f "$MAP_PATH_1" ]; then
    echo -e "${RED}错误: 未找到初始地图 $MAP_PATH_1${NC}"
    exit 1
fi

RVIZ_OPT="true"
[[ "$mode_choice" != "1" ]] && RVIZ_OPT="false"

ros2 launch rt_robot_nav2 rt_robot_nav2_complete.launch.py \
    use_slam:=false \
    use_nav:=true \
    map_file:=$MAP_PATH_1 \
    open_rviz:=$RVIZ_OPT &
NAV_PID=$!
PIDS+=($NAV_PID)
sleep 5

cd $WORKSPACE_DIR
source install/setup.bash
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
echo -e "${GREEN}✅ 系统就绪！初始楼层: 1F${NC}"

if [ "$mode_choice" == "3" ]; then
    echo -e "${YELLOW}📱 MQTT 房间导航模式已激活${NC}"
    echo -e "${CYAN}---------------------------------------------${NC}"
    echo -e "${GREEN}1. 发送房间号导航:${NC}"
    echo -e "   Topic: robot/nav_room"
    echo -e '   Payload: "101"  OR  {"room": "201"}'
    echo -e "${GREEN}2. 查看实时状态:${NC}"
    echo -e "   Topic: robot/status"
    echo -e "${GREEN}3. 电梯交互 (自动):${NC}"
    echo -e "   Topic: elevator/response"
    echo -e '   Payload: {"status": "arrived"}'
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
