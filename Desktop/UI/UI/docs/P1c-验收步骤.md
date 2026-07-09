# P1c 验收步骤 — 人检告警 + GUARD 视角跟踪 + PATROL 追人

> 前置：P1b 已通过；PC backend + RockPi Nav2/switcher 正常。

## PC 启动

```powershell
cd "E:\New folder1\002-701\002-701\Desktop\UI\UI\backend"
$env:MQTT_BRIDGE_ENABLED = "1"
$env:MQTT_ROBOT_ID = "robot01"
$env:MQTT_BROKER_HOST = "broker.emqx.io"
$env:SECURITY_MOCK_VISION = "1"   # 仅 Mock 验收时
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

## RockPi 启动（Nav2 + switcher 后）

```bash
cd ~/Desktop/rock_ws/ros_ws
colcon build --packages-select smart_nav_manager patrol_security
source install/setup.bash
export PATROL_SNAPSHOT_URL="http://<PC局域网IP>:8000/api/security/snapshot"
export PATROL_VISION_CAMERA=0
export PATROL_YOLO_MODEL=~/Desktop/rock_ws/ros_ws/person_detect_rknn/yolo11n.pt
bash scripts/start_patrol_security.sh
```

---

## 用例 1：告警 API + Web

1. 登录 `/security`
2. 用车端或 curl 上传 snapshot → Web 告警区出现缩略图

---

## 用例 2：GUARD 驻守控向 + 视角跟踪（不 Nav2 追人）

1. 首次模式 **驻守** → **进入巡逻模式** → 到起点后 `sub_state=guard`
2. 期望：**车静止**，不再无限自转
3. **驻守控向** 输入 `+30`（右正）→ **转向** → 车转到约 30° 后停，`guard_phase=idle`
4. 让人出现在相机前 → `sub_state=guard_view_track`，车头 bbox 水平居中（仅角速度，**不前进**）
5. 手动转向按钮应 **禁用**；人离开约 0.5s → 回 `guard`，可再次手动转向
6. Web 告警 + 提示音正常；状态 **不** 进入 Nav2 `track`

---

## 用例 3：PATROL 识人 → Nav2 TRACK → 恢复

1. 首次模式 **巡逻** → 启用计划 → 进入 PATROL
2. spin/巡逻中识人 → `sub_state=track`（Nav2 跟随，可带位移）
3. 丢失 → 360 → `lost_confirmed` → 回 resume 点续巡

---

## Mock（无相机 / 无真车）

- Web **Mock 识人** 仅模拟 PATROL→TRACK（需 `SECURITY_MOCK_VISION=1` 且已在 PATROL）
- GUARD 手动转向可在 mock 车端下测 API（`guard_phase` 变化）

---

## 通过标准

- [ ] GUARD：静止待命 + 手动角度转向（左负右正）
- [ ] GUARD 识人：视角跟踪，不 Nav2 TRACK，不前进
- [ ] 跟踪中禁手动转向；丢失后恢复待命
- [ ] PATROL 识人：Nav2 TRACK + 360 + 续巡
- [ ] 告警截图 + 实时 MJPEG 带检测框
