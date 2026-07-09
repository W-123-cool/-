import rclpy
from rclpy.node import Node
from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import PoseStamped
import json
import math
import paho.mqtt.client as mqtt

class MqttNavBridge(Node):
    def __init__(self):
        super().__init__('mqtt_nav_bridge')
        
        # --- MQTT 配置 ---
        self.broker = "broker.emqx.io"  # 公共Broker，也可改为局域网IP
        self.port = 1883
        self.topic_sub = "robot/nav_goal"
        
        # 初始化 MQTT
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        
        try:
            self.client.connect(self.broker, self.port, 60)
            self.client.loop_start()
            self.get_logger().info(f"MQTT Connected to {self.broker}")
        except Exception as e:
            self.get_logger().error(f"MQTT Connection Failed: {e}")

        # 初始化 Nav2 Action Client
        self.nav_client = None
        self._init_nav_client()

    def _init_nav_client(self):
        from rclpy.action import ActionClient
        self.nav_client = ActionClient(self, NavigateToPose, '/navigate_to_pose')
        self.get_logger().info("Waiting for Nav2 Action Server...")
        if self.nav_client.wait_for_server(timeout_sec=10.0):
            self.get_logger().info("Nav2 Action Server Found!")
        else:
            self.get_logger().warn("Nav2 Action Server NOT found. Navigation will fail.")

    def on_connect(self, client, userdata, flags, rc):
        self.get_logger().info(f"MQTT Connected with result code {rc}")
        client.subscribe(self.topic_sub)

    def on_message(self, client, userdata, msg):
        try:
            # 解析 JSON: {"x": 1.0, "y": 0.0, "yaw": 0.0}
            data = json.loads(msg.payload.decode())
            x = float(data['x'])
            y = float(data['y'])
            yaw = float(data.get('yaw', 0.0)) # 默认为0
            
            self.get_logger().info(f"Received Goal: x={x}, y={y}, yaw={yaw}")
            self.send_goal(x, y, yaw)
        except Exception as e:
            self.get_logger().error(f"Error parsing MQTT msg: {e}")

    def send_goal(self, x, y, yaw):
        if not self.nav_client or not self.nav_client.server_is_ready():
            self.get_logger().warn("Nav2 not ready, ignoring goal.")
            return

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id = 'map'
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        
        goal_msg.pose.pose.position.x = x
        goal_msg.pose.pose.position.y = y
        goal_msg.pose.pose.position.z = 0.0
        
        # 欧拉角(Yaw) 转 四元数(Z, W)
        # 假设 yaw 输入为弧度
        half_yaw = yaw / 2.0
        qz = math.sin(half_yaw)
        qw = math.cos(half_yaw)
        
        goal_msg.pose.pose.orientation.x = 0.0
        goal_msg.pose.pose.orientation.y = 0.0
        goal_msg.pose.pose.orientation.z = qz
        goal_msg.pose.pose.orientation.w = qw
        
        self.get_logger().info("Sending goal to Nav2...")
        self.nav_client.send_goal_async(goal_msg)

def main(args=None):
    rclpy.init(args=args)
    node = MqttNavBridge()
    rclpy.spin(node)
    node.client.loop_stop()
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
