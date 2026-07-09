# 深度相机辅助导航：TF + 深度转 LaserScan(/scan_depth)
# 需另开终端发布 /camera/depth/image_raw（Orbbec 仅深度模式）
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    pkg_dir = get_package_share_directory('depth_nav_assist')
    params_file = os.path.join(pkg_dir, 'config', 'depth_to_scan.yaml')

    depth_topic = LaunchConfiguration('depth_topic')
    depth_info_topic = LaunchConfiguration('depth_info_topic')
    scan_topic = LaunchConfiguration('scan_topic')
    camera_x = LaunchConfiguration('camera_x')
    camera_y = LaunchConfiguration('camera_y')
    camera_z = LaunchConfiguration('camera_z')

    return LaunchDescription([
        DeclareLaunchArgument(
            'depth_topic', default_value='/camera/depth/image_raw',
            description='Depth image topic from Orbbec driver'),
        DeclareLaunchArgument(
            'depth_info_topic', default_value='/camera/depth/camera_info',
            description='Depth camera_info topic'),
        DeclareLaunchArgument(
            'scan_topic', default_value='/scan_depth',
            description='Output LaserScan for Nav2 costmap fusion'),
        DeclareLaunchArgument('camera_x', default_value='0.15',
                              description='Camera x offset from base_link (m)'),
        DeclareLaunchArgument('camera_y', default_value='0.0'),
        DeclareLaunchArgument('camera_z', default_value='0.25'),

        LogInfo(msg='[depth_nav_assist] 仅深度模式：请在独立终端运行 scripts/start_camera.sh'),

        # base_link -> camera_link（按机器人实际安装位置修改 launch 参数）
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_to_camera_link_tf',
            arguments=[
                camera_x, camera_y, camera_z,
                '0', '0', '0',
                'base_link', 'camera_link',
            ],
            output='screen',
        ),
        # camera_link -> camera_depth_optical_frame（ROS 光学坐标系）
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='camera_link_to_depth_optical_tf',
            arguments=[
                '0', '0', '0',
                '-1.57079632679', '0', '-1.57079632679',
                'camera_link', 'camera_depth_optical_frame',
            ],
            output='screen',
        ),
        Node(
            package='depthimage_to_laserscan',
            executable='depthimage_to_laserscan_node',
            name='depthimage_to_laserscan',
            output='screen',
            parameters=[params_file],
            remappings=[
                ('depth', depth_topic),
                ('depth_camera_info', depth_info_topic),
                ('scan', scan_topic),
            ],
        ),
    ])
