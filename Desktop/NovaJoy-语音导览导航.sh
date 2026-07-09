#!/usr/bin/env bash
export DISPLAY="${DISPLAY:-:0}"
export CAR_CMD="/home/rock/Desktop/rock_ws/ros_ws/car_cmd.sh"
export CAR_CMD_PYTHON="/usr/bin/python3"
exec bash "/home/rock/Desktop/rock_ws/ros_ws/scripts/run_voice_nav_all.sh"
