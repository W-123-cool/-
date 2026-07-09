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
# ===========================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

PIDS=()

# 在独立图形终端启动 MicroROS Agent（主脚本 Ctrl+C 不关闭该窗口）
_start_microros_agent_terminal() {
    if ss -ulnp 2>/dev/null | grep -q ':8888'; then
        echo -e "${CYAN}端口 8888 已有 Agent 在运行，跳过重复启动（Agent 终端保持运行）。${NC}"
        return 0
    fi

    local agent_wrapper="/tmp/start_microros_agent_$$.sh"
    cat > "$agent_wrapper" << EOF
#!/bin/bash
cd "$MICROROS_WS" || { echo "错误: 无法进入 Microros 空间"; read -r; exit 1; }
source install/setup.bash
echo "=========================================="
echo " MicroROS Agent  udp4 :8888"
echo " 本窗口 Ctrl+C 可单独停止 Agent"
echo " 主脚本 Ctrl+C 不会关闭本窗口"
echo "=========================================="
ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888
echo ""
echo "Agent 已退出，按 Enter 关闭本窗口..."
read -r
EOF
    chmod +x "$agent_wrapper"

    if command -v gnome-terminal >/dev/null 2>&1; then
        gnome-terminal --title="MicroROS Agent :8888" -- "$agent_wrapper" &
        sleep 2
        echo -e "${GREEN}Agent 已在独立终端启动（退出主脚本不会停止 Agent）。${NC}"
        return 0
    fi
    if command -v xfce4-terminal >/dev/null 2>&1; then
        xfce4-terminal --title="MicroROS Agent :8888" -e "$agent_wrapper" &
        sleep 2
        echo -e "${GREEN}Agent 已在独立终端启动（退出主脚本不会停止 Agent）。${NC}"
        return 0
    fi
    if command -v mate-terminal >/dev/null 2>&1; then
        mate-terminal --title="MicroROS Agent :8888" -e "$agent_wrapper" &
        sleep 2
        echo -e "${GREEN}Agent 已在独立终端启动（退出主脚本不会停止 Agent）。${NC}"
        return 0
    fi
    if command -v lxterminal >/dev/null 2>&1; then
        lxterminal --title="MicroROS Agent :8888" -e "$agent_wrapper" &
        sleep 2
        echo -e "${GREEN}Agent 已在独立终端启动（退出主脚本不会停止 Agent）。${NC}"
        return 0
    fi
    if command -v xterm >/dev/null 2>&1; then
        xterm -T "MicroROS Agent :8888" -e "$agent_wrapper" &
        sleep 2
        echo -e "${GREEN}Agent 已在独立终端启动（退出主脚本不会停止 Agent）。${NC}"
        return 0
    fi

    # 无图形终端：回退后台启动（与旧行为一致）
    echo -e "${YELLOW}未找到图形终端，Agent 在后台启动（disown）。${NC}"
    bash "$agent_wrapper" &
    local agent_pid=$!
    disown "${agent_pid}" 2>/dev/null || true
    sleep 2
    echo -e "${GREEN}Agent 已启动 (PID ${agent_pid}，退出本脚本不会停止 Agent)。${NC}"
}

# 清理函数（仅停导航/传感器；MicroROS Agent 与 minicom 在独立会话，故意不杀）
cleanup() {
    echo -e "\n${YELLOW}[退出] 正在停止导航与传感器进程...${NC}"
    for pid in "${PIDS[@]}"; do
        kill -9 $pid >/dev/null 2>&1
    done
    pkill -f "ros2 launch" >/dev/null 2>&1
    pkill -f "smart_switcher" >/dev/null 2>&1
    pkill -f "smart_building_navigator" >/dev/null 2>&1
    echo -e "${CYAN}[保留] MicroROS Agent 仍在运行（避免 MCU 底盘会话断开）${NC}"
    echo -e "${CYAN}       若需停止 Agent，请在 Agent 终端单独 Ctrl+C${NC}"
    echo -e "${GREEN}[完成] 清理完毕。${NC}"
    exit 0
}

# 仅捕获 Ctrl+C (SIGINT)
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

source install/setup.bash
if [ $? -ne 0 ]; then
    echo -e "${RED}错误: 环境加载失败${NC}"
    exit 1
fi
echo -e "${GREEN}编译与环境加载完成。${NC}"

# ------------------------------------------------------------------------------
# 2. USB 配置
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[2] 配置 USB 权限...${NC}"
if [ -f "$USB_SETUP_SCRIPT" ]; then
    echo "$SUDO_PASSWORD" | sudo -S bash "$USB_SETUP_SCRIPT" > /dev/null 2>&1
    echo "$SUDO_PASSWORD" | sudo -S udevadm control --reload-rules > /dev/null 2>&1
    echo "$SUDO_PASSWORD" | sudo -S udevadm trigger > /dev/null 2>&1
    echo -e "${GREEN}USB 配置完成。${NC}"
fi

# ------------------------------------------------------------------------------
# 3. 启动传感器
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
# 4. MicroROS Agent（独立终端，主进程 Ctrl+C 后仍保留）
# ------------------------------------------------------------------------------
echo -e "\n${YELLOW}[4] 启动 MicroROS Agent...${NC}"
_start_microros_agent_terminal
# 不加入 PIDS：Agent 在独立终端/ disown 后台，Ctrl+C 时保留，防止 MCU 侧 microros 会话失效

# ------------------------------------------------------------------------------
# 5. 底盘连接提示
# ------------------------------------------------------------------------------
echo -e "\n${BLUE}==================================================${NC}"
echo -e "${BLUE}  【重要】MicroROS Agent 已在独立终端运行 (udp :8888)${NC}"
echo -e "${BLUE}  请在新终端连接底盘:${NC}"
echo -e "${BLUE}  minicom -D $SERIAL_PORT -b $BAUD_RATE${NC}"
echo -e "${BLUE}  输入: microros_chassis udp 10.10.10.31 8888${NC}"
echo -e "${BLUE}  输入: chassis_car_app${NC}"
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

# 根据模式决定是否打开 RViz
RVIZ_OPT="true"
[[ "$mode_choice" != "1" ]] && RVIZ_OPT="false"

# 启动 Nav2 (初始加载 1楼地图)
ros2 launch rt_robot_nav2 rt_robot_nav2_complete.launch.py \
    use_slam:=false \
    use_nav:=true \
    map_file:=$MAP_PATH_1 \
    open_rviz:=$RVIZ_OPT &
NAV_PID=$!
PIDS+=($NAV_PID)
sleep 5

# 启动智能楼层导航节点（落盘日志，默认 ~/logs/smart_switcher，保留最近 5 次）
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

# 根据模式显示不同提示
if [ "$mode_choice" == "3" ]; then
    echo -e "${YELLOW}📱 MQTT 房间导航模式已激活${NC}"
    echo -e "${CYAN}---------------------------------------------${NC}"
    echo -e "${GREEN}1. 发送房间号导航:${NC}"
    echo -e "   Topic: robot/nav_room"
    echo -e '   Payload: "101"  OR  {"room": "201"}'
    echo -e "${GREEN}2. 查看实时状态:${NC}"
    echo -e "   Topic: robot/status"
    echo -e "${GREEN}3. 电梯交互 (自动):${NC}"
    echo -e "   跨楼层时自动请求电梯，请模拟回复到:"
    echo -e "   Topic: elevator/response"
    echo -e '   Payload: {"status": "arrived"}'
    echo -e "${CYAN}---------------------------------------------${NC}"
elif [ "$mode_choice" == "2" ]; then
    echo -e "${YELLOW}⌨️  命令行交互模式已激活${NC}"
    echo -e "  格式: x y yaw (弧度). 输入 'q' 退出"
    
    # 进入命令行循环
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
        
        # 计算四元数并发送 Action
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

echo -e "${YELLOW}💡 按 Ctrl+C 停止导航/传感器（MicroROS Agent 保持运行）${NC}"
echo -e "${GREEN}=============================================${NC}"

# 保持脚本运行，等待 Ctrl+C
wait $NAV_PID
