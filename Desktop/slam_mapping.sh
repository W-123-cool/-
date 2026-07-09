#!/bin/bash
set -e

# ================= 配置区域 =================
WORKSPACE_DIR="$HOME/Desktop/rock_ws/ros_ws"
MICROROS_WS="$HOME/Desktop/rock_ws/microros_ws"
USB_SETUP_SCRIPT="$HOME/Desktop/rock_ws/ros_ws/usb_auto_setup.sh"
SUDO_PASSWORD="rock" 
DEFAULT_MAP_NAME="my_map3"
SERIAL_PORT="/dev/rt_shell"
BAUD_RATE=1500000
# ===========================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

PIDS=()
TERM_PID=""

cleanup() {
    echo -e "\n${YELLOW}[退出] 停止所有进程...${NC}"
    
    # 杀死所有记录的后台 ROS 进程
    for pid in "${PIDS[@]}"; do 
        kill -9 $pid >/dev/null 2>&1 || true
    done
    
    # 杀死单独开启的控制终端窗口
    if [ -n "$TERM_PID" ]; then
        kill -9 $TERM_PID >/dev/null 2>&1 || true
    fi

    # 清理残留进程
    pkill -f "micro_ros_agent" >/dev/null 2>&1 || true
    pkill -f "ros2 launch" >/dev/null 2>&1 || true
    pkill -f "keyboard_teleop" >/dev/null 2>&1 || true
    pkill -f "slam_toolbox" >/dev/null 2>&1 || true
    
    echo -e "${GREEN}[完成] 清理完毕。${NC}"
    exit 0
}
trap cleanup SIGINT EXIT

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  机器人 SLAM 建图启动脚本${NC}"
echo -e "${GREEN}========================================${NC}"

# 1. 编译与环境
echo -e "\n${YELLOW}[1] 编译 ROS2 工作空间...${NC}"
cd "$WORKSPACE_DIR"
colcon build > /dev/null 2>&1
source install/setup.bash
echo -e "${GREEN}编译完成。${NC}"

# 2. USB 配置
echo -e "\n${YELLOW}[2] 配置 USB 权限...${NC}"
if [ -f "$USB_SETUP_SCRIPT" ]; then
    echo "$SUDO_PASSWORD" | sudo -S bash "$USB_SETUP_SCRIPT" > /dev/null 2>&1
    echo "$SUDO_PASSWORD" | sudo -S udevadm control --reload-rules > /dev/null 2>&1
    echo "$SUDO_PASSWORD" | sudo -S udevadm trigger > /dev/null 2>&1
    echo -e "${GREEN}USB 配置完成。${NC}"
fi

# 3. 启动传感器
echo -e "\n${YELLOW}[3] 启动传感器 (IMU & Lidar)...${NC}"
ros2 launch dm_imu dm_imu_rviz.launch.py &
PIDS+=($!)
sleep 1
ros2 launch lslidar_driver lsn10p_launch.py &
PIDS+=($!)
sleep 2
echo -e "${GREEN}传感器已启动。${NC}"

# 4. MicroROS Agent
echo -e "\n${YELLOW}[4] 启动 MicroROS Agent...${NC}"
cd "$MICROROS_WS"
source install/setup.bash
ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888 &
PIDS+=($!)
sleep 2
echo -e "${GREEN}Agent 已启动。${NC}"

# 5. 底盘连接提示
echo -e "\n${BLUE}==================================================${NC}"
echo -e "${BLUE}  【重要】请在新终端连接底盘:${NC}"
echo -e "${BLUE}  minicom -D $SERIAL_PORT -b $BAUD_RATE${NC}"
echo -e "${BLUE}  输入: microros_chassis udp 10.10.10.31 8888${NC}"
echo -e "${BLUE}  输入: chassis_car_app${NC}"
echo -e "${BLUE}==================================================${NC}"
read -p "按 Enter 键确认底盘已就绪..."

# 6. 启动 SLAM 建图
echo -e "\n${YELLOW}[6] 启动 SLAM 建图模式...${NC}"
cd "$WORKSPACE_DIR"
source install/setup.bash

ros2 launch rt_robot_nav2 rt_robot_nav2_complete.launch.py \
    use_slam:=true \
    use_nav:=false \
    open_rviz:=true &
SLAM_PID=$!
PIDS+=($SLAM_PID)

sleep 3

# 7. 在【新终端窗口】启动键盘控制
echo -e "\n${YELLOW}[7] 正在打开新窗口启动键盘控制...${NC}"

# 构建在新终端中执行的命令字符串
# 注意：需要重新 source 环境变量，因为新终端是独立的 shell
CMD_IN_NEW_TERM="source $WORKSPACE_DIR/install/setup.bash && ros2 run chassis_controller keyboard_teleop"

# 使用 gnome-terminal 打开新窗口并执行命令
# -- 表示后续参数传递给 bash
gnome-terminal -- bash -c "$CMD_IN_NEW_TERM" &
TERM_PID=$!

# 等待一下确保窗口打开
sleep 2

echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN}✅ 建图环境已就绪！${NC}"
echo -e "${YELLOW}🎮 操作指南:${NC}"
echo -e "  1. 🟢 请在弹出的【新终端窗口】中使用键盘控制小车。"
echo -e "     (通常使用 I, J, K, L 或 W, A, S, D)"
echo -e "  2. 👀 观察 RViz 中的地图构建情况。"
echo -e "  3. 🏁 建图完成后，回到【此主终端窗口】按 ${RED}Enter${NC} 键。"
echo -e "${GREEN}=============================================${NC}"

read -p "建图完成？按 Enter 键保存地图并关闭控制窗口..."

# 8. 保存地图
echo -e "\n${YELLOW}[8] 正在保存地图...${NC}"
read -p "请输入地图名称 (默认: $DEFAULT_MAP_NAME): " input_map_name
MAP_NAME=${input_map_name:-$DEFAULT_MAP_NAME}

# 执行保存
ros2 launch rt_robot_nav2 save_map.launch.py map_name:=$MAP_NAME

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ 地图 '$MAP_NAME' 保存成功！${NC}"
    echo -e "${YELLOW}💡 地图文件通常位于: $WORKSPACE_DIR/install/rt_robot_nav2/share/rt_robot_nav2/map/${NC}"
else
    echo -e "${RED}❌ 地图保存失败，请检查日志。${NC}"
fi

# 触发清理退出（会关闭新开的控制窗口）
cleanup
