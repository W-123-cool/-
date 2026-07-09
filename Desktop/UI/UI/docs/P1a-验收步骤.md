# P1a 验收步骤

> 前置：在 `002-630/Desktop/UI/UI/backend` 启动后端；默认 **mock 车端**（未设 `MQTT_BRIDGE_ENABLED=1`）。

## 启动

```powershell
cd d:\cd\NovaJoy\002-630\Desktop\UI\UI\backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

浏览器打开：`http://127.0.0.1:8000/security`

## 用例 1：登录与状态

1. PIN 输入 `1234`（或 `SECURITY_OPERATOR_PIN`）→ 登录成功，进入主界面。
2. 状态 JSON 中 `mode_switch` 为 `off`，`mock_vehicle` 为 `true`（未开 MQTT 时）。

## 用例 2：进入巡逻 → mock 到起点 → 驻守

1. 首次模式选 **驻守**，n=1 → 点击 **进入巡逻模式**。
2. 期望：`security_active=true`，`sub_state=return_prep`，`message` 含「返回起点」。
3. 点击 **Mock 到起点（验收）**（或等待约 2s 自动 mock）。
4. 期望：`sub_state=guard`，顶栏 badge 为 GUARD，**车端静止不自转**。

## 用例 3：业务互斥

巡逻模式 ON 时（另开终端或取货/送货端）：

```powershell
curl http://127.0.0.1:8000/api/robot/state
```

期望：`security_active=true`，`capabilities.can_start_tour=false`，`can_courier_dispatch=false`。

取货 API（需先注册用户登录）应返回含「巡逻模式」的拒绝信息。

## 用例 4：退出与恢复

1. 点击 **退出巡逻模式**。
2. 期望：`mode_switch=off`；`robot_state` 为 `idle` 或 `pending_delivery`（视 SQLite 任务而定）。

## 用例 5：排班

1. 勾选启用排班，start/end 设为 **包含当前时刻** 的窗口 → 保存。
2. 若当前未在巡逻模式，约 1s 内应 **自动进入**（`entered_via=schedule`）。
3. 手动 **退出** → 本窗口内不再自动进入（`manual_block_auto_enter=true`）。
4. 手动 **进入** → 将排班 end 调到过去时间或等窗口结束 → 应 **自动退出**。

## 用例 6：API 直测（可选）

```powershell
# 登录
curl -X POST http://127.0.0.1:8000/api/security/login -H "Content-Type: application/json" -d "{\"pin\":\"1234\"}"

# 进入（替换 TOKEN）
curl -X POST http://127.0.0.1:8000/api/security/patrol/enter -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" -d "{\"first_mode\":\"guard\",\"patrol_rounds\":1}"

curl http://127.0.0.1:8000/api/security/status
```

## 通过标准

- [ ] enter → return_prep → guard（mock 或 mock-at-home）
- [ ] 巡逻 ON 时取货/导览/投件均被拒
- [ ] exit 后 security_active 清除
- [ ] 排班自动进/退与手动覆盖规则符合计划
- [ ] `/security` Web 页可完成上述操作

## MQTT 联调（可选，非 P1a 必测）

设 `MQTT_BRIDGE_ENABLED=1` 且车端在线时：

- 进入巡逻应发出 `nav_cancel` + `nav_room`（100）
- `robot/{id}/master/status` 收到 `master_mode` / `security_active`

此时需真车回 100 或仍用 **Mock 到起点** 按钮完成 RETURN_PREP。
