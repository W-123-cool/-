import rclpy
from rclpy.node import Node
from nav2_msgs.srv import LoadMap, ClearEntireCostmap
from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import PoseWithCovarianceStamped
from action_msgs.srv import CancelGoal
import math
import json
import time
import threading
from paho.mqtt.enums import CallbackAPIVersion
import paho.mqtt.client as mqtt
from rclpy.action import ActionClient

# ================= ⚠️ 全局配置区域 (已更新为最新坐标) =================

MAP_BASE_DIR = "/home/rock/Desktop/rock_ws/ros_ws/install/rt_robot_nav2/share/rt_robot_nav2/map/"

FLOOR_MAPS = {
    "1F": "my_map3.yaml",
    "2F": "my_map4.yaml",
}

# 注意：initial_pose 应设置为该楼层电梯出口的坐标（即小车从电梯出来后的位置）
ELEVATOR_POSITIONS = {
    "1F": {
        "entry": {"x": 0.693, "y": 5.5, "yaw": 0.0},          # 1楼去电梯的触发点
        "initial_pose": {"x": 0.693, "y": 5.5, "yaw": 0} # 从2楼回到1楼后的重定位点
    },
    "2F": {
        "entry": {"x": 0.00687, "y": 0.0118, "yaw": 0.0132},      # 2楼去电梯的触发点
        "initial_pose": {"x": 0.00687, "y": 0.0118, "yaw": 0.0132} # 从1楼去到2楼后的重定位点
    }
}

ROOM_LOCATIONS = {
    # 一楼房间
    "100": {"floor": "1F", "x": -0.254, "y": 0.551, "yaw": 0.0},
    "101": {"floor": "1F", "x": 3.73, "y": 7.94, "yaw": 0.0},
    "102": {"floor": "1F", "x": -3.24, "y": 7.32, "yaw": 0.0},
    "103": {"floor": "1F", "x": -0.235, "y": 10.4, "yaw": 0.0},
    "104": {"floor": "1F", "x": -2.79, "y": 3.86, "yaw": 0.0},
    
    # 二楼房间
    "200": {"floor": "2F", "x": 0.00687, "y": 0.0118, "yaw": 0.0},
    "201": {"floor": "2F", "x": 6.03, "y": -0.1, "yaw": 0.0},
    "202": {"floor": "2F", "x": 0.629, "y": 5.09, "yaw": 0.0},
    "203": {"floor": "2F", "x": 6.7, "y": 2.68, "yaw": 0.0},
    "204": {"floor": "2F", "x": 2.41, "y": 6.78, "yaw": 0.0},
}

MQTT_BROKER = "broker.emqx.io"
TOPIC_NAV_ROOM = "robot/nav_room"       
TOPIC_STATUS = "robot/status"           
TOPIC_ELEV_REQ = "elevator/request"     
TOPIC_ELEV_RESP = "elevator/response"   

# ==============================================================================

class SmartBuildingNavigator(Node):
    def __init__(self):
        super().__init__('smart_building_navigator')
        
        self.current_floor = "1F" 
        self.state = "IDLE"       
        self.current_goal_room = None
        self.target_next_floor = None
        
        # MQTT
        self.mqtt_client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION1)
        self.mqtt_client.on_connect = self.on_mqtt_connect
        self.mqtt_client.on_message = self.on_mqtt_message
        try:
            self.mqtt_client.connect(MQTT_BROKER, 1883, 60)
            self.mqtt_client.loop_start()
            self.get_logger().info("MQTT Connected")
        except Exception as e:
            self.get_logger().error(f"MQTT Error: {e}")

        # ROS2 Clients
        self.load_map_client = self.create_client(LoadMap, '/map_server/load_map')
        self.nav_client = ActionClient(self, NavigateToPose, '/navigate_to_pose')
        self.cancel_client = self.create_client(CancelGoal, '/navigate_to_pose/_action/cancel_goal')
        self.clear_global_costmap_client = self.create_client(ClearEntireCostmap, '/global_costmap/clear_entire_costmap')
        self.clear_local_costmap_client = self.create_client(ClearEntireCostmap, '/local_costmap/clear_entire_costmap')

        # Subs/Pubs
        self.pose_sub = self.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', self.pose_callback, 10)
        self.initial_pose_pub = self.create_publisher(PoseWithCovarianceStamped, '/initialpose', 10)
        self.current_pos = None
        
        # Status Thread
        self.status_timer = threading.Thread(target=self.publish_status_loop)
        self.status_timer.daemon = True
        self.status_timer.start()
        
        self.get_logger().info(f"System Initialized on Floor: {self.current_floor}")

    def on_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        client.subscribe(TOPIC_NAV_ROOM)
        client.subscribe(TOPIC_ELEV_RESP)

    def on_mqtt_message(self, client, userdata, msg):
        topic = msg.topic
        payload_str = msg.payload.decode()
        
        if topic == TOPIC_NAV_ROOM:
            self.handle_room_request(payload_str)
        elif topic == TOPIC_ELEV_RESP:
            self.handle_elevator_response(payload_str)

    def handle_room_request(self, payload_str):
        try:
            if payload_str.startswith('{'):
                data = json.loads(payload_str)
                room_id = str(data.get('room', ''))
            else:
                room_id = payload_str.strip()
            
            if room_id not in ROOM_LOCATIONS:
                self.get_logger().error(f"Unknown Room: {room_id}")
                return
            
            self.get_logger().info(f"Received Request for Room: {room_id}")
            self.navigate_to_room(room_id)
            
        except Exception as e:
            self.get_logger().error(f"Parse Error: {e}")

    def navigate_to_room(self, room_id):
        if self.state not in ["IDLE", "NAVIGATING_TO_ROOM"]:
            self.get_logger().warn(f"System busy ({self.state}), ignoring new request.")
            return

        target_info = ROOM_LOCATIONS[room_id]
        target_floor = target_info['floor']
        self.current_goal_room = room_id
        
        if target_floor == self.current_floor:
            self.get_logger().info(f"Same Floor ({target_floor}). Navigating directly to {room_id}.")
            self.send_nav_goal(target_info['x'], target_info['y'], target_info['yaw'])
            self.state = "NAVIGATING_TO_ROOM"
        else:
            self.get_logger().info(f"Cross Floor: {self.current_floor} -> {target_floor}. Going to Elevator.")
            self.target_next_floor = target_floor
            self.go_to_elevator()

    def go_to_elevator(self):
        self.state = "GOING_TO_ELEVATOR"
        elev_entry = ELEVATOR_POSITIONS[self.current_floor]['entry']
        self.send_nav_goal(elev_entry['x'], elev_entry['y'], elev_entry['yaw'])

    def pose_callback(self, msg):
        self.current_pos = msg.pose.pose
        
        if self.state == "GOING_TO_ELEVATOR" and self.current_pos:
            elev_entry = ELEVATOR_POSITIONS[self.current_floor]['entry']
            dist = math.sqrt(
                (self.current_pos.position.x - elev_entry['x'])**2 +
                (self.current_pos.position.y - elev_entry['y'])**2
            )
            
            if dist < 1.0:
                self.get_logger().info("!!! Arrived at Elevator Entrance !!!")
                self.request_elevator()

    def request_elevator(self):
        self.state = "WAITING_ELEVATOR"
        
        if self.cancel_client.service_is_ready():
            self.cancel_client.call_async(CancelGoal.Request())
            self.get_logger().info("🛑 Stopped at Elevator.")
        
        req_payload = {
            "current_floor": self.current_floor,
            "target_floor": self.target_next_floor,
            "action": "call"
        }
        self.mqtt_client.publish(TOPIC_ELEV_REQ, json.dumps(req_payload))
        self.get_logger().info(f"📡 Sent Elevator Request")

    def handle_elevator_response(self, payload_str):
        if self.state != "WAITING_ELEVATOR":
            return
            
        try:
            data = json.loads(payload_str)
            if data.get('status') == 'arrived':
                self.get_logger().info("✅ Elevator Arrived! Switching Map...")
                self.switch_floor_map(self.target_next_floor)
        except:
            pass

    def switch_floor_map(self, new_floor):
        self.state = "SWITCHING_MAP"
        
        map_file = FLOOR_MAPS.get(new_floor)
        if not map_file:
            self.get_logger().error(f"No map found for floor {new_floor}")
            self.state = "IDLE"
            return
            
        req = LoadMap.Request()
        req.map_url = MAP_BASE_DIR + map_file
        future = self.load_map_client.call_async(req)
        future.add_done_callback(lambda fut: self.on_map_loaded(fut, new_floor))

    def on_map_loaded(self, future, new_floor):
        try:
            resp = future.result()
            if resp.result == LoadMap.Response().RESULT_SUCCESS:
                self.get_logger().info(f"✅ Map for {new_floor} Loaded.")
                
                # 1. 重定位
                relocal_pose = ELEVATOR_POSITIONS[new_floor]['initial_pose']
                self.force_relocalize(relocal_pose)
                
                # 2. 【关键优化】等待 3 秒，让雷达扫描并更新 Costmap
                self.get_logger().info("⏳ Waiting for sensor data to update costmaps...")
                time.sleep(3.0)
                
                # 3. 清除旧地图残留
                if self.clear_global_costmap_client.service_is_ready():
                    self.clear_global_costmap_client.call_async(ClearEntireCostmap.Request())
                if self.clear_local_costmap_client.service_is_ready():
                    self.clear_local_costmap_client.call_async(ClearEntireCostmap.Request())
                
                # 4. 再次等待 1 秒，确保清除生效
                time.sleep(1.0)

                # 5. 更新楼层状态
                self.current_floor = new_floor
                self.get_logger().info(f"📍 Current Floor Updated to: {self.current_floor}")

                # 6. 【关键优化】发送一个微小的“唤醒”目标，激活规划器
                # 直接在重定位点附近发送一个 0.5米 的目标，防止长距离规划失败
                wake_x = relocal_pose['x'] + 0.5 * math.cos(relocal_pose['yaw'])
                wake_y = relocal_pose['y'] + 0.5 * math.sin(relocal_pose['yaw'])
                self.get_logger().info("🚀 Sending wake-up goal...")
                self.send_nav_goal(wake_x, wake_y, relocal_pose['yaw'])
                
                # 7. 等待小车动起来后再发送最终目标 (简单延时策略)
                # 更好的做法是监听 Action 反馈，但这里用延时简化
                time.sleep(2.0) 
                
                # 8. 发送最终房间目标
                final_dest = ROOM_LOCATIONS[self.current_goal_room]
                self.get_logger().info(f"🚀 Resuming navigation to {self.current_goal_room}")
                self.send_nav_goal(final_dest['x'], final_dest['y'], final_dest['yaw'])
                self.state = "NAVIGATING_TO_ROOM"
                
            else:
                self.get_logger().error("❌ Map Load Failed.")
                self.state = "IDLE"
        except Exception as e:
            self.get_logger().error(f"Switch Error: {e}")
            self.state = "IDLE"

    def force_relocalize(self, pose_dict):
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = 'map'
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = pose_dict['x']
        msg.pose.pose.position.y = pose_dict['y']
        msg.pose.pose.orientation.z = math.sin(pose_dict['yaw'] / 2.0)
        msg.pose.pose.orientation.w = math.cos(pose_dict['yaw'] / 2.0)
        msg.pose.covariance[0] = 0.01
        msg.pose.covariance[7] = 0.01
        msg.pose.covariance[35] = 0.01
        
        for _ in range(5):
            self.initial_pose_pub.publish(msg)
            time.sleep(0.1)
        self.get_logger().info(f"📍 Relocalized to {self.current_floor}")

    def send_nav_goal(self, x, y, yaw):
        if not self.nav_client.server_is_ready():
            return
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id = 'map'
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = x
        goal_msg.pose.pose.position.y = y
        goal_msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        goal_msg.pose.pose.orientation.w = math.cos(yaw / 2.0)
        self.nav_client.send_goal_async(goal_msg)

    def publish_status_loop(self):
        while rclpy.ok():
            status_msg = {
                "state": self.state,
                "current_floor": self.current_floor,
                "current_room": self.current_goal_room
            }
            self.mqtt_client.publish(TOPIC_STATUS, json.dumps(status_msg))
            time.sleep(3)

def main(args=None):
    rclpy.init(args=args)
    node = SmartBuildingNavigator()
    from rclpy.executors import MultiThreadedExecutor
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
