# 集成提示词：取货端手机 Web 版（`/pickup`）

> **用途**：将此文件与本次改动的代码一并带到目标机器，交给开发者或 AI Agent 做合并集成。  
> **来源机器功能**：取货端 Android APK 在部分网络环境（如路由器 AP 隔离）下无法连 PC 后端；新增浏览器版取货端作为替代方案。  
> **原则**：本功能为**增量添加**，不修改现有 API、不替换 Kivy/APK 取货端，与目标机器上其它并行开发的功能应可共存。

---

## 一、给 AI / 集成人员的提示词（可直接复制）

```
请在 NovaJoy 工程中集成「取货端手机 Web 版」功能，要求如下：

【背景】
- 现有取货端为 Kivy APK（user_client_mobile）和 PC 版（user_client），通过 HTTP REST 调 backend。
- 部分环境下手机 APK 无法连 PC 后端，但手机浏览器能打开 http://<PC_IP>:8000/api/robot/state 看到 JSON。
- 因此新增与 /onboard 同模式的 Web 单页：手机浏览器打开 /pickup 即可完成注册、登录、发起取货、确认取货，无需 APK。

【本次改动范围 — 仅 3 处，均为增量】
1. 新增文件：UI/frontend/pickup.html（自包含 HTML+CSS+JS，移动端 Bottom Nav 三页：首页/任务/我的）
2. 修改文件：UI/backend/main.py — 在 @app.get("/onboard") 路由函数之后、@app.get("/api/bridge/status") 之前，插入 @app.get("/pickup") 路由（见下文代码块）
3. 更新文档：UI/frontend/readme.md — 补充 /pickup 入口说明（可选，若目标机已有其它 readme 内容则合并表格行即可）

【集成规则 — 必须遵守】
- 不要删除或重写 user_client_mobile、user_client、user_app.html 占位页。
- 不要修改 /api/auth/*、/api/pickup/*、/api/user/*、/api/robot/state 等已有 API 签名与逻辑。
- pickup.html 通过同源 fetch 调现有 REST，Authorization 头格式为 Bearer <token>，与 Kivy 客户端一致。
- pickup.html 依赖已有静态资源路由 /ui-assets/{filename}（b.png、无字图标.png），不要新建静态资源路由。
- main.py 中 _FRONTEND_DIR 已存在，pickup_page 实现方式必须与 onboard_page、security_ui_page 保持一致（read_text + HTMLResponse + charset=utf-8）。
- 若 main.py 在目标分支已有其它人在 onboard 与 api/bridge/status 之间插入了新路由，将 pickup_page 紧挨 onboard_page 之后即可，顺序不影响功能。
- 不新增 Python 依赖、不修改 requirements.txt。

【main.py 需插入的代码（若目标文件无此路由则整段添加）】

@app.get("/pickup")
def pickup_page() -> HTMLResponse:
    """取货端手机 Web UI（浏览器打开，无需 APK）。"""
    path = _FRONTEND_DIR / "pickup.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="frontend/pickup.html not found")
    return HTMLResponse(
        content=path.read_text(encoding="utf-8"),
        media_type="text/html",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )

【pickup.html 功能要点 — 集成后请核对】
- 三 Tab：首页（机器人状态）、任务（发起取货 + 确认取货 + 任务列表）、我的（注册/登录/退出）
- API 根地址默认 location.origin（与 /onboard 相同）；localStorage 键 nvj_pickup_token / nvj_pickup_user / nvj_pickup_api
- 调用的接口：POST /api/auth/register、POST /api/auth/login、POST /api/pickup/request、POST /api/pickup/verify、GET /api/user/tasks、GET /api/robot/state
- 每 3 秒轮询刷新；401 时清 token
- viewport 移动端适配；样式与 onboard.html 同系（深色玻璃态 + b.png 背景）

【验收步骤 — 全部通过才算集成成功】
1. 启动后端：cd UI/backend && python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
2. PC 浏览器 http://127.0.0.1:8000/pickup → 200，页面有「首页/任务/我的」底栏
3. 「我的」注册新用户 → toast 成功，显示已登录
4. 「任务」填门牌号提交取货 → 返回 task_id 与 dropoff_code
5. GET /api/user/tasks 与页面任务列表一致
6. 手机浏览器 http://<PC局域网IP>:8000/pickup 同样可用（与 /onboard 同级联调方式）
7. 确认未破坏：/onboard、/security、原有 Kivy 取货端仍正常

【冲突处理】
- 若 frontend/pickup.html 已存在：对比功能完整性（三 Tab + 六类 API），以本次版本为准或手动合并
- 若 main.py 已有 @app.get("/pickup")：检查实现是否与上文一致，勿重复注册路由
- 若 frontend/readme.md 有冲突：保留双方 Web 入口表格行，确保含 /pickup 一行

完成后请列出实际修改/新增的文件路径，并说明验收结果。
```

---

## 二、需携带的文件清单

| 操作 | 路径（相对 `UI/` 根目录） |
|------|---------------------------|
| **新增** | `frontend/pickup.html` |
| **修改** | `backend/main.py`（+12 行路由） |
| **可选更新** | `frontend/readme.md` |
| **本文档** | `docs/集成提示词-取货Web端.md` |

**不需要携带的文件**（本功能未改动）：
- `user_client/`、`user_client_mobile/`、`courier_client/`
- `backend` 除 `main.py` 外的所有模块
- `requirements.txt`、`buildozer.spec`

---

## 三、main.py 合并锚点

在目标 `backend/main.py` 中定位：

```
@app.get("/onboard")
def onboard_page() -> HTMLResponse:
    ...
    )   ← onboard 函数结束

# ===== 在此处插入 pickup_page =====

@app.get("/api/bridge/status")
def api_bridge_status() -> ...
```

插入后 `pickup_page` 与 `onboard_page` 结构对称，便于 code review。

---

## 四、与 APK 取货端的关系

| 对比项 | Kivy APK（user_client_mobile） | Web `/pickup` |
|--------|-------------------------------|---------------|
| 安装 | 需 Buildozer 打包 | 无需安装，浏览器书签即可 |
| API 配置 | App「我的」页手动填 IP | 默认同源，高级可改 localStorage |
| 网络要求 | 手机 → PC 直连 HTTP | 与浏览器访问 JSON 相同 |
| 业务 API | 同一套 REST | 同一套 REST |
| 适用场景 | 正式部署、离线打包 | 联调、AP 隔离、临时用手机取货 |

两者**并行存在**，不互相替代；集成时勿删 APK 相关代码。

---

## 五、目标机器快速验收命令

```powershell
# PC
cd <工程路径>/Desktop/UI/UI/backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1

# 另开终端
curl -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/pickup
# 期望 200

curl -s http://127.0.0.1:8000/api/health
# 期望 {"status":"ok"}
```

手机浏览器：`http://<PC_IP>:8000/pickup`

---

## 六、版本信息

- **功能名称**：取货端手机 Web 版
- **路由**：`GET /pickup`
- **依赖后端版本**：已有 FastAPI 取货 API（`/api/auth/*`、`/api/pickup/*`）
- **新增依赖**：无
- **编写日期**：2026-07-06
