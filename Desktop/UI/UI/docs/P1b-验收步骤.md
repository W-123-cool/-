# P1b 验收步骤 — 巡逻计划 + 逐点下发 + 车端执行

> 前置：P1a 已通过；`patrol_out` 目录已有 JSON（如 `single/patrol_1F.json`）。

## 环境

### Mock 验收（无真车）

```powershell
cd d:\cd\NovaJoy\002-630\Desktop\UI\UI\backend
# 勿设 MQTT_BRIDGE_ENABLED
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### 真车验收

```powershell
$env:MQTT_BRIDGE_ENABLED="1"
$env:MQTT_ROBOT_ID="robot01"
$env:MQTT_BROKER_HOST="broker.emqx.io"
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

车端需 **重新 colcon build 并重启** `switcher_node`（含 P1b 的 `patrol_nav_waypoint` 处理）。

```bash
cd ~/Desktop/ros_ws
colcon build --packages-select smart_nav_manager
# 重启 start_multi_map.sh / switcher
```

---

## 用例 1：计划列表与 MapSync

1. 打开 `http://127.0.0.1:8000/security` 登录。
2. **巡逻计划** 下拉应列出 `patrol_out` 下 JSON（如 `single/patrol_1F.json`）。
3. 选择较短计划（如 manual 的 3～4 点）→ **启用计划**。
4. 期望：提示「地图校验通过」或 mock 警告；叠加图（若有 PNG）显示路线点。

---

## 用例 2：Mock 完整巡逻一圈

1. 首次模式 **巡逻**，n=1。
2. **启用计划** 后 → **进入巡逻模式**。
3. Mock 到起点（或等 ~2s）。
4. 期望：
   - `sub_state=patrol`
   - `patrol_route.route_pos` 递增直至 `route_len`
   - 完成后 `sub_state=end_return` → 再 mock ~2s → `guard`
5. 顶栏状态行显示 `路线 x/y`。

---

## 用例 3：真车逐点导航（MQTT）

1. 启用 MQTT；车上 switcher 在线。
2. 选同层计划（`map_yaml` 与车当前层一致）。
3. 进入巡逻 → 车到 100 后进入 PATROL。
4. 期望：
   - MQTT `robot/robot01/request` 出现 `patrol_nav_waypoint`
   - 车端 Nav2 逐点到达
   - `spin_360` 点：到站后原地约一圈（`/cmd_vel`）
   - `robot/robot01/status` 回 `patrol_waypoint_done`
5. Web 状态 `route_pos` 随 done 递增。

---

## 用例 4：地图不一致拦截

1. 真车联调时，选与车上 **不同 yaml** 的计划（或改计划 floor）。
2. **启用计划** 应 **400 拒绝**（地图不一致）。

---

## 用例 5：心跳扩展（真车）

`GET /api/security/status` → `robot.mqtt` 应含（P1b 车端）：

- `pose_x`, `pose_y`, `pose_yaw`
- `current_map_yaml`

---

## 通过标准

- [ ] 可选计划 + 启用 + 地图预览
- [ ] MapSync 失败时拒绝启用/开巡
- [ ] Mock：PATROL 跑完 route_order → END_RETURN → GUARD
- [ ] 真车：MQTT 逐点 + spin + done 回传（至少 3 点同层走通）
- [ ] 心跳含 pose / map_yaml

---

## 常见问题

| 现象 | 处理 |
|------|------|
| 进入 PATROL 后提示未选计划 | 先 **启用计划** |
| 路线不走 | 查 MQTT 连接；mock 时每点约 1.5s |
| spin 不动 | 查 `/cmd_vel` 与底盘；可调 `PATROL_SPIN_WZ` |
| 叠加图 401 | 重新登录；img 带 token 参数 |
