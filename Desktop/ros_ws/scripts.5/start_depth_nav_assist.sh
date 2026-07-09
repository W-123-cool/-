#!/bin/bash
# 单独启动深度→激光转换（调试用；正常由 Nav2 bringup 自动启动）
export ROS_DOMAIN_ID=0
source /opt/ros/foxy/setup.bash
source "$HOME/Desktop/rock_ws/ros_ws/install/setup.bash"

echo "启动 depth_nav_assist -> /scan_depth"
ros2 launch depth_nav_assist depth_nav_assist.launch.py
