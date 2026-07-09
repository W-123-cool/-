## 一、车端（RockPi）需要同步的代码

PC 源目录：`E:\New folder1\002-701\002-701\Desktop\`  
车端目标：`~/Desktop/rock_ws/ros_ws/`（脚本在 `~/Desktop/`）

### 1. 必同步（P1c 完整功能）

| 类别 | PC 路径 | 车端路径 | 作用 |
|------|---------|----------|------|
| **整包** | `ros_ws/src/patrol_security/` | `src/patrol_security/` | 视觉、GUARD 视角跟踪、MJPEG、`/frame.jpg` |
| **单文件** | `ros_ws/src/smart_nav_manager/smart_nav_manager/switcher_node.py` | 同上 | GUARD 时 cancel Nav2、转发 `/patrol_security/guard_cmd_vel` → `/cmd_vel` |
| **脚本** | `ros_ws/scripts/start_patrol_security.sh` | `scripts/start_patrol_security.sh` | 释放 8089、校验 `PATROL_SNAPSHOT_URL` |
| **脚本** | `Desktop/start_multi_map.sh` | `~/Desktop/start_multi_map.sh` | Agent 独立终端（可选但建议） |

### 2. `patrol_security` 包内关键文件

```
patrol_security/
├── patrol_security/
│   ├── patrol_vision_node.py    ← GUARD 跟踪、guard_cmd_vel、MJPEG
│   ├── tracker_control.py       ← bbox 居中 PID
│   ├── person_selector.py
│   ├── mjpeg_server.py          ← /stream + /frame.jpg + CORS
│   ├── mqtt_helper.py
│   ├── patrol_track_assist_node.py
│   └── __init__.py
├── setup.py / package.xml / resource/
```

### 3. 不必拷到车（只在 PC）

| 文件 | 说明 |
|------|------|
| `UI/UI/frontend/security/index.html` | 保安 Web 实时画面 |
| `UI/UI/backend/main.py` | PC 代理、MQTT 优先级 |
| `UI/UI/backend/patrol_mode/engine.py` | PC 状态机、`guard_view_track` 下发 |

---

### 4. 车端编译与重启

```bash
cd ~/Desktop/rock_ws/ros_ws
colcon build --packages-select patrol_security smart_nav_manager
source install/setup.bash
```

**终端 1 — 导航（含新 switcher）**

```bash
bash ~/Desktop/start_multi_map.sh
# Agent 独立窗口 → minicom 连底盘 → 主脚本按 Enter 继续
```

**终端 2 — 视觉**

```bash
cd ~/Desktop/rock_ws/ros_ws
source install/setup.bash

export PATROL_SNAPSHOT_URL="http://192.168.1.41:8000/api/security/snapshot"   # 勿用 <pc_ip> 占位符 （ip是本机ip一般）
export PATROL_VISION_CAMERA=0
export PATROL_YOLO_MODEL=~/Desktop/rock_ws/ros_ws/person_detect_rknn/yolo11n.pt

pkill -9 -f patrol_vision_node; pkill -9 -f patrol_track_assist
fuser -k 8089/tcp 2>/dev/null || true
sleep 1
bash scripts/start_patrol_security.sh
```

**PC 后端**

```powershell
cd "E:\New folder1\002-701\002-701\Desktop\UI\UI\backend"
$env:MQTT_BRIDGE_ENABLED="1"
$env:MQTT_ROBOT_ID="robot01"
$env:MQTT_BROKER_HOST="broker.emqx.io"
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

---

## 二、完整验收步骤（P1c）

IP 示例：PC `192.168.1.41`，车 `10.1.24.7`（按实际替换）。

---

### 阶段 0：冒烟（5 分钟）

| # | 操作 | 通过标准 |
|---|------|----------|
| 0.1 | RockPi：`pgrep -af patrol_vision_node` | 有进程 |
| 0.2 | `curl -s -o /tmp/f.jpg -w "%{http_code}" http://127.0.0.1:8089/frame.jpg` | `200` |
| 0.3 | PC 浏览器 `http://10.1.24.7:8089/stream` | 带框实时画面 |
| 0.4 | PC 启动 backend 无 `MQTT 桥启动失败` | MQTT 在线 |
| 0.5 | `http://192.168.1.41:8000/security` 登录 PIN `1234` | 成功 |

---

### 阶段 1：实时画面（保安 UI）

1. 选 **直连车端**
2. URL：`http://10.1.24.7:8089/stream` → **连接实时画面**（**Ctrl+F5** 硬刷新）
3. **通过：** 提示「直连 MJPEG 已连接」，左侧有带框画面  
4. **备选：** PC 代理模式 + `PATROL_CAMERA_STREAM_URL` 设车端 stream

---

### 阶段 2：告警截图

1. **驻守** → **进入巡逻模式** → 到起点 `sub_state=guard`
2. 人站相机前 2～3 秒
3. **通过：** Web 告警缩略图 + 提示音；无 `%3cpc_ip%3e` 类 snapshot 报错  
4. RockPi 日志无 `snapshot upload failed`（`PATROL_SNAPSHOT_URL` 须为真实 PC IP）

---

### 阶段 3：GUARD 静止 + 手动转向

1. 到起点后 **车静止**，不无限自转  
2. **驻守控向** 输入 `+30` → **转向**  
3. **通过：** 车转约 30° 后停；`guard_phase` → `idle`  
4. **←15° / 15°→** 按钮可用

---

### 阶段 4：GUARD 识人 → 视角跟踪（核心）

**前置：** 已同步 `switcher_node.py` + `patrol_vision_node.py`，且 **重启过 `start_multi_map.sh`**

1. GUARD 待命，人站画面 **偏左或偏右**（勿卡在中间黄/青线死区）
2. **通过（Web）：** `sub_state=guard_view_track`，手动转向 **禁用**
3. **通过（车）：** **只转不走**，人框移向画面中央
4. **通过（日志）：**

```text
guard view_track: person detected
guard view_track err=... wz=...
```

5. **RockPi 自检：**

```bash
ros2 topic echo /patrol_security/guard_cmd_vel   # 应有非零 angular.z
ros2 topic echo /cmd_vel                         # guard 期间应有非零 angular.z
```

6. 人离开 ≥0.5s → 回 `guard` / `idle`，可再次手动转向

**若 wz 有值但车不动：**

```bash
ros2 topic pub /cmd_vel geometry_msgs/msg/Twist "{angular: {z: -0.35}}" -r 20
```

- 能转 → switcher 未更新或未重启导航  
- 不能转 → 查 MicroROS / 底盘 / minicom

**若转反：**

```bash
export PATROL_GUARD_INVERT_ANGULAR=1
bash scripts/start_patrol_security.sh
```

---

### 阶段 5：PATROL 识人 → Nav2 TRACK

1. **首次模式 = 巡逻**，`n=1`，启用同层计划 → **进入巡逻模式**
2. `sub_state=patrol`，状态行有 `路线 x/y`
3. 巡逻中识人 → `sub_state=track`（可有位移）
4. 人离开 → 360° 扫描 → 回上一巡逻点续巡

---

### 阶段 6：通过清单（勾选）

**启动与画面**

- [ ] `patrol_vision_node` + `patrol_track_assist` 正常
- [ ] `/stream`、`/frame.jpg` 可访问
- [ ] 保安 UI 直连有画面
- [ ] snapshot 无占位符 URL 错误

**GUARD**

- [ ] 到起点静止，不自转
- [ ] 手动角度转向（左负右正）
- [ ] 识人视角跟踪，bbox 居中，不前进
- [ ] 跟踪中禁手动转向；丢失后恢复待命
- [ ] 不进 Nav2 `track`

**PATROL**

- [ ] 逐点巡逻正常
- [ ] 识人 → `track` → 丢失 → 续巡

**告警**

- [ ] JPEG 告警 + Web 展示 + 提示音

---

## 三、建议验收顺序（约 45～60 分钟）

```
同步代码 → colcon build → 重启 start_multi_map + patrol_security
    → 阶段 0 冒烟
    → 阶段 1 画面
    → 阶段 2 告警
    → 阶段 3 手动转向
    → 阶段 4 视角跟踪（+ ros2 topic 自检）
    → 阶段 5 PATROL→TRACK
    → 勾选清单
```

---

## 四、一键拷贝（PC PowerShell，改 IP）

```powershell
$ROCK="rock@10.1.24.7"
$SRC="E:\New folder1\002-701\002-701\Desktop"

scp -r "$SRC\ros_ws\src\patrol_security" "${ROCK}:~/Desktop/rock_ws/ros_ws/src/"
scp "$SRC\ros_ws\src\smart_nav_manager\smart_nav_manager\switcher_node.py" "${ROCK}:~/Desktop/rock_ws/ros_ws/src/smart_nav_manager/smart_nav_manager/"
scp "$SRC\ros_ws\scripts\start_patrol_security.sh" "${ROCK}:~/Desktop/rock_ws/ros_ws/scripts/"
scp "$SRC\start_multi_map.sh" "${ROCK}:~/Desktop/"
```

验收时若某一步失败，发：**现象 + `ros2 topic echo /cmd_vel` 一行 + patrol_vision 含 `guard view_track` 的日志**，便于继续排查。




下面是 **002-701 语音导览 + Web UI 导览联调** 的完整启动顺序（含底盘）。按 **PC 先启后端 → 车端再启导航栈+语音 → 最后开 UI** 来操作。

---

## 架构与终端分工

| 终端 | 位置 | 内容 |
|------|------|------|
| PC-1 | PC | FastAPI 后端 + MQTT 桥 |
| 车-主 | RockPi | 语音 agent（终端1，前台） |
| 车-2 | RockPi | **底盘 minicom**（手动输入命令） |
| 车-3 | RockPi | MicroROS Agent (UDP 8888) |
| 车-4 | RockPi | 本地大模型 flask :8001（云端模式可跳过） |
| 车-5 | RockPi | Nav2 + smart_switcher |
| 浏览器 | PC 或车载屏 | Web 导览 UI `/onboard?tab=tour` |

> 车端脚本路径（RockPi）：`~/Desktop/rock_ws/ros_ws/scripts/`  
> PC 工程路径：`E:\New folder1\002-701\002-701\Desktop\`

---

## 第 0 步：PC 启动后端（必须先做）

语音 UI 按键模式（`VOICE_INPUT_MODE=ui`）和 Web 导览都依赖后端 `/onboard` 与 `/api/*`。

**PowerShell（PC）：**

```powershell
cd "E:\New folder1\002-701\002-701\Desktop\UI\UI\backend"

$env:MQTT_BRIDGE_ENABLED = "1"
$env:MQTT_ROBOT_ID = "robot01"
$env:MQTT_BROKER_HOST = "broker.emqx.io"
$env:MQTT_BROKER_PORT = "1883"

python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

**验收：**

```powershell
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/onboard
```

记下 PC 局域网 IP，例如 `192.168.1.41`，后面车端要用。

---

## 第 1 步：RockPi 一键启动（推荐）

`run_voice_nav_all.sh` 会按正确时序自动开终端 2～5，并在主终端启语音。

**RockPi 终端（主终端）：**

```bash
# 若后端在 PC 上，必须指向 PC IP（不能写 127.0.0.1）
export COURIER_API_BASE="http://192.168.1.41:8000"
export VOICE_TOUR_API_BASE="$COURIER_API_BASE"
export VOICE_INPUT_MODE="ui"
export VOICE_NAV_ROBOT_ID="robot01"

cd ~/Desktop/rock_ws/ros_ws/scripts
bash run_voice_nav_all.sh
```

或桌面快捷方式：

```bash
bash ~/Desktop/NovaJoy-语音导览导航.sh
```

### 脚本内部时序（自动执行，无需手动拆）

```
[1] colcon build
[0] 复制 car_cmd
[1] USB 权限
[2] 终端3 — MicroROS Agent (UDP 8888)
[3] 终端2 — 底盘 minicom  ← 需人工操作
[4] 终端4 — flask LLM :8001（云端模式可能跳过）
[5] 预热 IMU/雷达
[7] 终端5 — Nav2 + smart_switcher
[8] 等待 nav_action_bridge 就绪
[9] 大模型自检
[10] 终端1 — 语音 agent（UI 按键 / 唤醒词）
```

---

## 第 2 步：底盘连接（终端2，关键手动步骤）

脚本会弹出 **「终端2-底盘minicom」**，在 `msh />` 提示符下输入：

```text
microros_chassis udp <RockPi_IP> 8888
chassis_car_app
```

`<RockPi_IP>` 为 RockPi 本机 IP（脚本会打印，通常 `hostname -I` 第一个地址，例如 `10.10.10.31`）。

**成功标志：** 出现 `ROS CAR START SUCCESSFULLY`

然后回到 **主终端按 Enter** 继续后续 Nav2 / 语音启动。

> 串口默认：`/dev/rt_shell`，波特率 `1500000`  
> 若串口不存在，脚本会先跑 `usb_auto_setup.sh`

---

## 第 3 步：打开 Web 导览 UI

Nav2 + smart_switcher + 语音 agent 都就绪后，打开车载 Web 导览页。

**方式 A — PC 浏览器（联调常用）：**

```
http://192.168.1.41:8000/onboard?tab=tour
```

**方式 B — RockPi 车载屏浏览器：**

```bash
export COURIER_API_BASE="http://192.168.1.41:8000"
bash ~/Desktop/rock_ws/ros_ws/scripts/open_onboard_web.sh
```

**方式 C — 集成脚本（Nav 栈已跑、只想补启后台语音）：**

```bash
export COURIER_API_BASE="http://192.168.1.41:8000"
export ONBOARD_OPEN_WEB=1
bash ~/Desktop/rock_ws/ros_ws/scripts/start_tour_integrated.sh
```

---

## 联调交互流程

1. **UI 点选导览**：Web 页选房间 → `tour_nav` → MQTT → `smart_switcher` → Nav2 导航  
2. **语音导览**：唤醒词「你好小诺」→ Web 点「语音输入」开始 → 说话 → 再点结束 → 大模型解析 → 同样走 MQTT/Nav2  
3. **到站**：点「确认到达并返航」或「确认取消导览」→ 回 100 房间

---

## 启动后快速自检

**PC：**

```powershell
curl http://192.168.1.41:8000/api/bridge/status
curl http://192.168.1.41:8000/api/robot/state
```

**RockPi：**

```bash
pgrep -af smart_switcher          # 应有 switcher 进程
pgrep -af voice_to_nav_agent      # 应有语音 agent
pgrep -af micro_ros_agent         # MicroROS 在跑
ros2 topic hz /scan               # 雷达有数据
```

**语音 agent 日志应出现：**

```text
[UI-PTT] backend=http://192.168.1.41:8000
[UI-PTT] backend reachable
```

若 unreachable，检查 `COURIER_API_BASE` 是否指向 PC 真实 IP，以及 PC 防火墙是否放行 8000。

---

## 精简版命令清单（复制用）

```powershell
# ===== PC 终端1 =====
cd "E:\New folder1\002-701\002-701\Desktop\UI\UI\backend"
$env:MQTT_BRIDGE_ENABLED="1"; $env:MQTT_ROBOT_ID="robot01"
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

```bash
# ===== RockPi 主终端 =====
export COURIER_API_BASE="http://192.168.1.41:8000"
export VOICE_INPUT_MODE="ui"
bash ~/Desktop/rock_ws/ros_ws/scripts/run_voice_nav_all.sh

# ===== RockPi 终端2（minicom 内手动）=====
microros_chassis udp 10.10.10.31 8888
chassis_car_app
# → 回主终端按 Enter

# ===== 浏览器 =====
# http://192.168.1.41:8000/onboard?tab=tour
```

---

## 常见变体

| 场景 | 调整 |
|------|------|
| 后端也在 RockPi 本机 | `COURIER_API_BASE=http://127.0.0.1:8000`，先 `bash ~/Desktop/NovaJoy-启动后端.sh` |
| 纯云端大模型、无本地 flask | `voice_nav_env.sh` 已默认 `VOICE_NAV_BACKEND=auto`，有网走 DashScope |
| 跳过底盘确认提示（已连好） | `AI_CAR_SKIP_CHASSIS_PROMPT=1 bash run_voice_nav_all.sh` |
| 用 Kivy 车载屏代替 Web | `python -m onboard_client.main`（Web 方案是当前推荐路径） |

把示例 IP `192.168.1.41` / `10.10.10.31` 换成你实际的 PC IP 和 RockPi IP 即可。若你告诉我当前 PC 和 RockPi 的 IP，我可以按你的环境生成一份可直接粘贴的版本。