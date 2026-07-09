"""
FastAPI 入口：用户鉴权、取货请求、通知查询；开发用模拟送货/送达接口。
运行：在 backend 目录下执行
    python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from database import init_db
from mqtt_robot_bridge import bridge_enabled, get_bridge
from task_manager import (
    courier_try_dispatch,
    create_pickup_request,
    apply_mqtt_task_status,
    debug_clear_all_tasks_and_notifications_idle,
    get_robot_snapshot,
    list_notifications,
    list_tasks_for_courier,
    list_user_tasks,
    mark_notification_read,
    robot_mark_delivered,
    simulate_robot_return_home_complete,
    user_verify_pickup,
)
from tour_manager import (
    apply_tour_arrived,
    cancel_tour,
    enter_holding_from_nav_stop,
    finish_tour,
    get_tour_status,
    holding_cancel_confirm,
    poll_tour_arrival,
    seize_from_delivery_return,
    simulate_arrived,
    start_tour,
    voice_discard,
    voice_set_pending_room,
    voice_touch,
    voice_wake,
    voice_utterance,
    start_tour_from_voice,
)
from master_takeover import on_master_takeover_enter, on_master_takeover_release
from patrol_mode.auth import login as security_login, verify_token as security_verify_token
from patrol_mode.models import PatrolTaskConfig
from patrol_mode.service import (
    get_patrol_service,
    list_schedules,
    replace_schedules,
    start_patrol_tick_loop,
)
from patrol_mode.plan_service import list_plan_catalog, load_selected_plan, plan_preview_payload, save_selected_plan
from patrol_mode.patrol_executor import get_patrol_executor
from patrol_mode.map_sync import check_map_sync
from patrol_mode.config import DEFAULT_PATROL_OUT, PATROL_UPLOAD_KEY, mock_vision_enabled, mock_vehicle_enabled
from patrol_mode.alerts import get_alert_store
from vehicle_rooms import list_building_catalog
from user_module import create_session, normalize_username, register_user, resolve_session, verify_login
from voice_ptt import (
    ptt_awake_sync,
    ptt_begin,
    ptt_end,
    ptt_set_final,
    ptt_set_partial,
    ptt_sleep,
    ptt_status,
    ptt_tap,
)

app = FastAPI(title="楼内送取货调试 API", version="0.1.0")

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
_UI_ROOT = _FRONTEND_DIR.parent

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    if bridge_enabled():
        br = get_bridge()
        br.set_task_status_handler(apply_mqtt_task_status)
        br.set_tour_arrived_handler(apply_tour_arrived)
        try:
            br.start()
        except Exception:
            import logging

            logging.getLogger("uvicorn.error").exception(
                "MQTT 桥启动失败（请检查网络与 MQTT_BROKER_*）；桥已禁用直至进程重启"
            )
    from patrol_mode.service import init_patrol_integrations

    init_patrol_integrations()
    start_patrol_tick_loop()


@app.on_event("shutdown")
def _shutdown() -> None:
    if bridge_enabled():
        try:
            get_bridge().stop()
        except Exception:
            pass


def _token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    return authorization.split(" ", 1)[1].strip()


def current_user_id(authorization: Optional[str] = Header(None)) -> int:
    uid = resolve_session(_token(authorization))
    if not uid:
        raise HTTPException(status_code=401, detail="未登录或令牌无效")
    return uid


# --- 请求体模型 ---


class RegisterBody(BaseModel):
    username: str = Field(..., description="用户名")
    login_password: str = Field(..., description="登录密码（登录与到站取件）")


class LoginBody(BaseModel):
    username: str
    login_password: str


class PickupRequestBody(BaseModel):
    door_plate: str = Field(..., description="门牌号")


class PickupVerifyBody(BaseModel):
    task_id: str
    login_password: str


class CourierSimBody(BaseModel):
    match_key: str = Field(..., description="送货员输入的 6 位投件码")


class TourStartBody(BaseModel):
    room: str = Field(..., description="导览目标房间号，与 switcher ROOM_LOCATIONS 一致")
    discard_voice: bool = Field(
        True,
        description="为 true 时丢弃进行中的待语音会话（UI 确认导览抢占）",
    )


class TourVoicePendingRoomBody(BaseModel):
    room: str = Field(..., description="待语音态已解析、尚未发车的房间号")


class TourVoiceUtteranceBody(BaseModel):
    text: str = Field(..., description="语音识别文本")
    intent: str = Field("", description="navigate|qa|cancel|end_session|unknown")
    room: str = Field("", description="导航房间号")


class TourVoicePttPartialBody(BaseModel):
    text: str = ""


class TourVoicePttFinalBody(BaseModel):
    text: str = ""


class MasterTakeoverReleaseBody(BaseModel):
    snapshot: dict[str, object] = Field(default_factory=dict)


# --- 路由 ---


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ui-assets/{filename:path}")
def ui_assets(filename: str) -> FileResponse:
    """UI 背景与图标（b.png、*图标.png 等）。"""
    base = _UI_ROOT.resolve()
    path = (base / filename).resolve()
    if not str(path).startswith(str(base)) or not path.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(path)


@app.get("/b.png", include_in_schema=False)
def legacy_b_png() -> FileResponse:
    """兼容旧 onboard.html 相对路径 ../b.png -> /b.png。"""
    return ui_assets("b.png")


@app.get("/onboard")
def onboard_page() -> HTMLResponse:
    """车载极简 Web UI（单文件 HTML，无 Kivy）。"""
    path = _FRONTEND_DIR / "onboard.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="frontend/onboard.html not found")
    return HTMLResponse(
        content=path.read_text(encoding="utf-8"),
        media_type="text/html",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )


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


@app.get("/api/bridge/status")
def api_bridge_status() -> dict[str, object]:
    """MQTT 桥状态（MQTT_BRIDGE_ENABLED=0 时 mqtt 字段为 null）。"""
    if not bridge_enabled():
        return {"mqtt_bridge_enabled": False, "mqtt": None}
    try:
        return {"mqtt_bridge_enabled": True, "mqtt": get_bridge().snapshot()}
    except Exception as e:
        return {"mqtt_bridge_enabled": True, "mqtt": None, "error": str(e)}


@app.post("/api/auth/register")
def api_register(body: RegisterBody) -> dict[str, object]:
    ok, msg, uid = register_user(body.username, body.login_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    token = create_session(int(uid))  # type: ignore[arg-type]
    return {
        "token": token,
        "user_id": uid,
        "username": normalize_username(body.username),
        "message": msg,
    }


@app.post("/api/auth/login")
def api_login(body: LoginBody) -> dict[str, object]:
    ok, msg, uid = verify_login(body.username, body.login_password)
    if not ok or uid is None:
        raise HTTPException(status_code=400, detail=msg)
    token = create_session(uid)
    return {
        "token": token,
        "user_id": uid,
        "message": msg,
        "username": normalize_username(body.username),
    }


@app.post("/api/pickup/request")
def api_pickup_request(
    body: PickupRequestBody, authorization: Optional[str] = Header(None)
) -> dict[str, object]:
    uid = current_user_id(authorization)
    ok, msg, tid, dropoff_code = create_pickup_request(uid, body.door_plate)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "task_id": tid, "dropoff_code": dropoff_code}


@app.get("/api/user/tasks")
def api_user_tasks(authorization: Optional[str] = Header(None)) -> dict[str, object]:
    uid = current_user_id(authorization)
    return {"tasks": list_user_tasks(uid)}


@app.get("/api/user/notifications")
def api_notifications(authorization: Optional[str] = Header(None)) -> dict[str, object]:
    uid = current_user_id(authorization)
    return {"items": list_notifications(uid)}


class NotifReadBody(BaseModel):
    notification_id: int


@app.post("/api/user/notifications/read")
def api_notif_read(
    body: NotifReadBody, authorization: Optional[str] = Header(None)
) -> dict[str, object]:
    uid = current_user_id(authorization)
    if not mark_notification_read(uid, body.notification_id):
        raise HTTPException(status_code=404, detail="通知不存在")
    return {"ok": True}


@app.post("/api/pickup/verify")
def api_pickup_verify(
    body: PickupVerifyBody, authorization: Optional[str] = Header(None)
) -> dict[str, object]:
    uid = current_user_id(authorization)
    ok, msg, meta = user_verify_pickup(uid, body.task_id, body.login_password)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    out: dict[str, object] = {"message": msg}
    if meta:
        out.update(meta)
    return out


@app.get("/api/robot/state")
def api_robot_state() -> dict[str, object]:
    return get_robot_snapshot()


@app.get("/api/building/rooms")
def api_building_rooms() -> dict[str, object]:
    """楼层/房间目录（从 ros_ws switcher_node 解析，与真车地图一致）。"""
    out = list_building_catalog()
    out["mqtt_bridge_enabled"] = bridge_enabled()
    return out


@app.get("/api/tour/status")
def api_tour_status() -> dict[str, object]:
    poll_tour_arrival(timeout=0.0)
    st = get_tour_status()
    st["mqtt_bridge_enabled"] = bridge_enabled()
    return st


@app.post("/api/tour/start")
def api_tour_start(body: TourStartBody) -> dict[str, object]:
    ok, msg, data = start_tour(body.room, discard_voice=body.discard_voice)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "data": data}


@app.post("/api/tour/voice/wake")
def api_tour_voice_wake() -> dict[str, object]:
    ok, msg, data = voice_wake()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "data": data}


@app.post("/api/tour/voice/discard")
def api_tour_voice_discard() -> dict[str, object]:
    ok, msg = voice_discard()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


@app.post("/api/tour/voice/touch")
def api_tour_voice_touch() -> dict[str, object]:
    ok, msg = voice_touch()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


@app.post("/api/tour/voice/pending-room")
def api_tour_voice_pending_room(body: TourVoicePendingRoomBody) -> dict[str, object]:
    ok, msg = voice_set_pending_room(body.room)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


@app.post("/api/tour/holding/cancel")
def api_tour_holding_cancel() -> dict[str, object]:
    ok, msg = holding_cancel_confirm()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


@app.post("/api/tour/nav/stop-holding")
def api_tour_nav_stop_holding() -> dict[str, object]:
    """导览途中截停 → 原地待机（P3 语音截停 / 联调）。"""
    ok, msg = enter_holding_from_nav_stop()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


@app.post("/api/tour/simulate/arrived")
def api_tour_simulate_arrived() -> dict[str, object]:
    ok, msg = simulate_arrived()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


@app.post("/api/tour/voice/utterance")
def api_tour_voice_utterance(body: TourVoiceUtteranceBody) -> dict[str, object]:
    ok, msg = voice_utterance(intent=body.intent, room=body.room, text=body.text)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "status": get_tour_status()}


@app.get("/api/tour/voice/ptt/status")
def api_tour_voice_ptt_status() -> dict[str, object]:
    return ptt_status()


@app.post("/api/tour/voice/ptt/tap")
def api_tour_voice_ptt_tap() -> dict[str, object]:
    ok, msg, action = ptt_tap()
    tour_msg: Optional[str] = None
    if action == "wake":
        tw_ok, tw_msg, _ = voice_wake()
        tour_msg = tw_msg if tw_ok else tw_msg
    return {
        "ok": ok,
        "message": msg,
        "action": action,
        "tour_message": tour_msg,
        "status": ptt_status(),
    }


@app.post("/api/tour/voice/ptt/awake-sync")
def api_tour_voice_ptt_awake_sync() -> dict[str, object]:
    ok, msg = ptt_awake_sync()
    return {"ok": ok, "message": msg, "status": ptt_status()}


@app.post("/api/tour/voice/ptt/sleep")
def api_tour_voice_ptt_sleep() -> dict[str, object]:
    ok, msg = ptt_sleep()
    return {"ok": ok, "message": msg, "status": ptt_status()}


@app.post("/api/tour/voice/ptt/begin")
def api_tour_voice_ptt_begin() -> dict[str, object]:
    ok, msg = ptt_begin()
    return {"ok": ok, "message": msg, "status": ptt_status()}


@app.post("/api/tour/voice/ptt/end")
def api_tour_voice_ptt_end() -> dict[str, object]:
    ok, msg = ptt_end()
    return {"ok": ok, "message": msg, "status": ptt_status()}


@app.post("/api/tour/voice/ptt/partial")
def api_tour_voice_ptt_partial(body: TourVoicePttPartialBody) -> dict[str, object]:
    ok, msg = ptt_set_partial(body.text)
    return {"ok": ok, "message": msg, "status": ptt_status()}


@app.post("/api/tour/voice/ptt/final")
def api_tour_voice_ptt_final(body: TourVoicePttFinalBody) -> dict[str, object]:
    ok, msg = ptt_set_final(body.text)
    return {"ok": ok, "message": msg, "status": ptt_status()}


@app.post("/api/tour/voice/seize-delivery-return")
def api_tour_voice_seize() -> dict[str, object]:
    ok, msg, data = seize_from_delivery_return()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "data": data}


@app.post("/api/master/takeover/enter")
def api_master_takeover_enter() -> dict[str, object]:
    ok, msg, snap = on_master_takeover_enter()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "snapshot": snap}


@app.post("/api/master/takeover/release")
def api_master_takeover_release(body: MasterTakeoverReleaseBody) -> dict[str, object]:
    ok, msg, result = on_master_takeover_release(body.snapshot or None)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "result": result}


@app.post("/api/tour/finish")
def api_tour_finish() -> dict[str, object]:
    ok, msg = finish_tour()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "deprecated": "请使用 POST /api/tour/holding/cancel"}


@app.post("/api/tour/cancel")
def api_tour_cancel() -> dict[str, object]:
    ok, msg = cancel_tour()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "deprecated": "请使用 POST /api/tour/holding/cancel"}


# --- 送货员端 HTTP（与 /api/dev/* 调用同一套逻辑，便于 UI 拆分；生产应加鉴权） ---


@app.get("/api/courier/queue")
def api_courier_queue() -> dict[str, object]:
    return {"tasks": list_tasks_for_courier()}


@app.post("/api/courier/confirm")
def api_courier_confirm(body: CourierSimBody) -> dict[str, object]:
    ok, msg, data = courier_try_dispatch(body.match_key)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "data": data}


@app.post("/api/courier/mark-delivered/{task_id}")
def api_courier_mark_delivered(task_id: str) -> dict[str, object]:
    ok, msg = robot_mark_delivered(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


def _courier_return_home_response() -> dict[str, object]:
    ok, msg, data = simulate_robot_return_home_complete()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    out: dict[str, object] = {"message": msg}
    out.update(data)
    return out


@app.post("/api/courier/robot/return-home")
def api_courier_robot_return_home() -> dict[str, object]:
    """仅在机器人处于 returning 时：模拟回位结束 → idle 或 pending_delivery。"""
    return _courier_return_home_response()


@app.post("/api/courier/robot/reset")
def api_courier_robot_reset_alias() -> dict[str, object]:
    """与 return-home 相同（保留旧路径）。"""
    return _courier_return_home_response()


@app.post("/api/courier/debug/clear-all-tasks")
def api_courier_debug_clear_all() -> dict[str, object]:
    """调试：清空全部任务与通知，机器人初态。"""
    return debug_clear_all_tasks_and_notifications_idle()


# --- 本地模拟（与 /api/courier/* 等价，保留兼容） ---


@app.post("/api/dev/courier/confirm")
def dev_courier_confirm(body: CourierSimBody) -> dict[str, object]:
    return api_courier_confirm(body)


@app.post("/api/dev/robot/delivered/{task_id}")
def dev_robot_delivered(task_id: str) -> dict[str, object]:
    return api_courier_mark_delivered(task_id)


@app.post("/api/dev/robot/reset")
def dev_robot_reset() -> dict[str, object]:
    """兼容旧路径：等同于「模拟回位」。"""
    return _courier_return_home_response()


@app.post("/api/dev/debug/clear-all-tasks")
def dev_debug_clear_all() -> dict[str, object]:
    return api_courier_debug_clear_all()


# --- 保安/总控巡逻模式 P1a ---


def _security_auth(authorization: Optional[str] = Header(None)) -> None:
    tok = _token(authorization)
    if not security_verify_token(tok):
        raise HTTPException(status_code=401, detail="请先登录保安总控（PIN）")


class SecurityLoginBody(BaseModel):
    pin: str = ""


class PatrolEnterBody(BaseModel):
    first_mode: str = Field(default="guard", description="guard | patrol")
    patrol_rounds: int = Field(default=1, ge=0)
    guard_between_min: int = Field(default=5, ge=0)
    guard_yaw: Optional[float] = None


class PatrolTaskConfigBody(BaseModel):
    first_mode: str = "guard"
    patrol_rounds: int = Field(default=1, ge=0)
    guard_between_min: int = Field(default=5, ge=0)
    guard_yaw: float = 0.134
    plan_dir: str = ""


class SchedulesBody(BaseModel):
    schedules: list[dict[str, object]] = Field(default_factory=list)


@app.get("/security", response_class=HTMLResponse)
def security_ui_page() -> HTMLResponse:
    path = _FRONTEND_DIR / "security" / "index.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="security UI 未找到")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.post("/api/security/login")
def api_security_login(body: SecurityLoginBody) -> dict[str, object]:
    ok, msg, token = security_login(body.pin)
    if not ok or not token:
        raise HTTPException(status_code=401, detail=msg)
    return {"message": msg, "token": token}


def _mqtt_camera_stream_url() -> str:
    """车端 MQTT 上报的相机流（IP 变化时以车端为准）。"""
    if not bridge_enabled():
        return ""
    try:
        mqtt = (get_bridge().snapshot().get("mqtt") or {})
        if isinstance(mqtt, dict) and mqtt.get("camera_stream_url"):
            return str(mqtt.get("camera_stream_url")).strip()
    except Exception:
        pass
    return ""


def _vehicle_camera_stream_upstream() -> str:
    """车端 MJPEG 上游地址：优先 MQTT 上报，其次 PC 环境变量。"""
    from patrol_mode.config import PATROL_CAMERA_STREAM_URL

    mqtt_url = _mqtt_camera_stream_url()
    if mqtt_url:
        return mqtt_url
    return (PATROL_CAMERA_STREAM_URL or "").strip()


def _vehicle_camera_frame_upstream() -> str:
    """车端单帧 JPEG 地址（/frame.jpg）。"""
    stream = _vehicle_camera_stream_upstream()
    if not stream:
        return ""
    base = stream.split("?", 1)[0].rstrip("/")
    if base.endswith("/stream"):
        return base[: -len("/stream")] + "/frame.jpg"
    return base + "/frame.jpg"


@app.get("/api/security/status")
def api_security_status() -> dict[str, object]:
    svc = get_patrol_service()
    out = svc.status_dict()
    out["robot"] = get_robot_snapshot()
    from patrol_mode.config import PATROL_CAMERA_STREAM_URL

    stream = _vehicle_camera_stream_upstream()
    frame = _vehicle_camera_frame_upstream()
    mqtt_stream = _mqtt_camera_stream_url()
    out["camera_stream_upstream"] = stream
    out["camera_stream_url"] = stream
    out["camera_stream_direct_url"] = stream
    out["camera_stream_mqtt_url"] = mqtt_stream
    out["camera_stream_configured_url"] = (PATROL_CAMERA_STREAM_URL or "").strip()
    out["camera_frame_direct_url"] = frame
    out["camera_stream_mode_default"] = "direct"
    out["camera_proxy_url"] = "/api/security/camera/stream"
    out["camera_frame_proxy_url"] = "/api/security/camera/frame"
    return out


@app.get("/api/security/camera/stream")
def api_security_camera_stream(
    token: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    """经 PC 后端转发车端 MJPEG，保安前端只连 :8000。"""
    import urllib.error
    import urllib.request

    tok = _token(authorization) or (token or "").strip()
    if not security_verify_token(tok):
        raise HTTPException(status_code=401, detail="未登录或 token 无效")
    upstream = _vehicle_camera_stream_upstream()
    if not upstream:
        raise HTTPException(
            status_code=503,
            detail="未配置车端相机流（PC 设 PATROL_CAMERA_STREAM_URL 或车端 MQTT 上报 patrol_camera_stream）",
        )
    req = urllib.request.Request(upstream, headers={"User-Agent": "NovaJoySecurity/1.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
    except urllib.error.URLError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"无法连接车端流 {upstream}: {exc}",
        ) from exc

    def iter_upstream():
        try:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            resp.close()

    media = resp.headers.get_content_type() or "multipart/x-mixed-replace; boundary=frame"
    return StreamingResponse(iter_upstream(), media_type=media)


@app.get("/api/security/camera/frame")
def api_security_camera_frame(
    token: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    """转发车端最新一帧 JPEG（同源 img 轮询，兼容 person_tracker 式预览）。"""
    import urllib.error
    import urllib.request

    tok = _token(authorization) or (token or "").strip()
    if not security_verify_token(tok):
        raise HTTPException(status_code=401, detail="未登录或 token 无效")
    upstream = _vehicle_camera_frame_upstream()
    if not upstream:
        raise HTTPException(
            status_code=503,
            detail="未配置车端相机（设 PATROL_CAMERA_STREAM_URL=http://车IP:8089/stream）",
        )
    req = urllib.request.Request(upstream, headers={"User-Agent": "NovaJoySecurity/1.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        data = resp.read()
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"无法拉取车端帧 {upstream}: {exc}") from exc
    if not data:
        raise HTTPException(status_code=503, detail="车端无帧数据")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


class GuardRotateBody(BaseModel):
    delta_deg: float = Field(..., description="相对当前朝向增量，左负右正（度）")


class VisionTogglesBody(BaseModel):
    patrol_track_enabled: Optional[bool] = Field(None, description="巡逻中 Nav2 追人")
    guard_view_track_enabled: Optional[bool] = Field(None, description="驻守视角跟人（仅角速度）")


class VisionConfBody(BaseModel):
    detection_conf: float = Field(..., ge=0.05, le=0.95, description="YOLO 识人置信度阈值")


@app.post("/api/security/guard/rotate")
def api_security_guard_rotate(
    body: GuardRotateBody,
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    ok, msg, data = get_patrol_service().guard_rotate(body.delta_deg)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "patrol": data}


@app.post("/api/security/guard/rotate/cancel")
def api_security_guard_rotate_cancel(
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    ok, msg, data = get_patrol_service().guard_rotate_cancel()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "patrol": data}


@app.get("/api/security/vision-settings")
def api_security_vision_settings(
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    return {"vision_settings": get_patrol_service().vision_settings_dict()}


@app.put("/api/security/vision-settings/toggles")
def api_security_vision_toggles(
    body: VisionTogglesBody,
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    ok, msg, data = get_patrol_service().update_vision_toggles(
        patrol_track_enabled=body.patrol_track_enabled,
        guard_view_track_enabled=body.guard_view_track_enabled,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "vision_settings": data.get("vision_settings"), "patrol": data}


@app.post("/api/security/vision-settings/apply-conf")
def api_security_vision_apply_conf(
    body: VisionConfBody,
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    ok, msg, data = get_patrol_service().apply_detection_conf(body.detection_conf)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "vision_settings": data.get("vision_settings"), "patrol": data}


@app.post("/api/security/patrol/enter")
def api_security_patrol_enter(
    body: PatrolEnterBody,
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    ok, msg, data = get_patrol_service().enter(
        first_mode=body.first_mode,
        patrol_rounds=body.patrol_rounds,
        guard_between_min=body.guard_between_min,
        guard_yaw=body.guard_yaw,
        via="manual",
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "patrol": data}


@app.post("/api/security/patrol/exit")
def api_security_patrol_exit(
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    ok, msg, data = get_patrol_service().exit(via="manual")
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "patrol": data}


@app.get("/api/security/schedules")
def api_security_schedules_list(
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    return {"schedules": list_schedules()}


@app.put("/api/security/schedules")
def api_security_schedules_save(
    body: SchedulesBody,
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    replace_schedules(body.schedules)
    return {"schedules": list_schedules()}


@app.get("/api/security/task-config")
def api_security_task_config(
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    return {"task": get_patrol_service().state.task.to_dict()}


@app.put("/api/security/task-config")
def api_security_task_config_save(
    body: PatrolTaskConfigBody,
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    cfg = PatrolTaskConfig.from_dict(body.model_dump())
    get_patrol_service().update_task_config(cfg)
    return {"task": cfg.to_dict()}


class PlanSelectBody(BaseModel):
    path: str = Field(..., description="patrol JSON 绝对或相对路径")
    id: str = ""


@app.get("/api/security/plans")
def api_security_plans(
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    root = str(DEFAULT_PATROL_OUT)
    return {
        "root": root,
        "plans": list_plan_catalog(root),
        "selected": load_selected_plan(),
    }


@app.post("/api/security/plans/select")
def api_security_plans_select(
    body: PlanSelectBody,
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    ok, msg, detail = get_patrol_executor().select_plan(body.path, body.id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    plan = load_selected_plan() or {}
    return {"message": msg, "detail": detail, "preview": plan_preview_payload(plan)}


@app.get("/api/security/plans/preview")
def api_security_plans_preview(
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    plan = load_selected_plan()
    if not plan:
        raise HTTPException(status_code=404, detail="未选择巡逻计划")
    ok, msg, sync = check_map_sync(plan)
    return {"ok": ok, "message": msg, "sync": sync, "preview": plan_preview_payload(plan)}


@app.get("/api/security/map/overlay")
def api_security_map_overlay(
    path: str,
    token: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    tok = _token(authorization) or (token or "").strip()
    if not security_verify_token(tok):
        raise HTTPException(status_code=401, detail="请先登录")
    p = Path(path).expanduser().resolve()
    root = DEFAULT_PATROL_OUT.resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="路径不在 patrol_out 目录内")
    if not p.is_file():
        raise HTTPException(status_code=404, detail="叠加图不存在")
    return FileResponse(str(p), media_type="image/png")


@app.post("/api/security/dev/mock-at-home")
def api_security_dev_mock_at_home(
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    """Mock 验收：强制 RETURN_PREP → GUARD/PATROL。"""
    _security_auth(authorization)
    ok, msg = get_patrol_service().dev_mock_at_home()
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg, "patrol": get_patrol_service().status_dict()}


@app.post("/api/security/snapshot")
async def api_security_snapshot_upload(
    file: UploadFile = File(...),
    robot_id: str = Form(default="robot01"),
    patrol_epoch: int = Form(default=0),
    sub_state_hint: str = Form(default=""),
    floor: str = Form(default=""),
    confidence: float = Form(default=0.0),
    pose_x: Optional[float] = Form(default=None),
    pose_y: Optional[float] = Form(default=None),
    pose_yaw: Optional[float] = Form(default=None),
    bbox: str = Form(default=""),
    upload_key: Optional[str] = Header(None, alias="X-Patrol-Upload-Key"),
) -> dict[str, object]:
    if PATROL_UPLOAD_KEY and (upload_key or "").strip() != PATROL_UPLOAD_KEY:
        raise HTTPException(status_code=403, detail="upload key 无效")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    meta: dict[str, object] = {
        "robot_id": robot_id,
        "patrol_epoch": patrol_epoch,
        "sub_state_hint": sub_state_hint,
        "floor": floor,
        "confidence": confidence,
        "pose_x": pose_x,
        "pose_y": pose_y,
        "pose_yaw": pose_yaw,
        "source": "vehicle",
    }
    if bbox.strip():
        try:
            import json as _json

            meta["bbox"] = _json.loads(bbox)
        except Exception:
            meta["bbox"] = bbox
    entry = get_alert_store().add_alert(jpeg_bytes=data, meta=meta)
    return {"ok": True, "alert": entry}


@app.get("/api/security/alerts")
def api_security_alerts_list(
    limit: int = 50,
    offset: int = 0,
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    return get_alert_store().list_alerts(limit=min(limit, 100), offset=max(offset, 0))


@app.post("/api/security/alerts/mark-read")
def api_security_alerts_mark_read(
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    n = get_alert_store().mark_read()
    return {"marked": n}


@app.delete("/api/security/alerts/{alert_id}")
def api_security_alert_delete(
    alert_id: str,
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    ok, msg = get_alert_store().delete_alert(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail=msg)
    return {"ok": True, "message": msg, "alerts_total": get_alert_store().total_count()}


@app.post("/api/security/alerts/delete-all")
def api_security_alerts_delete_all(
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    _security_auth(authorization)
    n, msg = get_alert_store().delete_all()
    return {"ok": True, "deleted": n, "message": msg}


@app.get("/api/security/alerts/{alert_id}/image")
def api_security_alert_image(
    alert_id: str,
    token: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    tok = _token(authorization) or (token or "").strip()
    if not security_verify_token(tok):
        raise HTTPException(status_code=401, detail="请先登录")
    p = get_alert_store().get_image_path(alert_id)
    if not p:
        raise HTTPException(status_code=404, detail="告警图不存在")
    return FileResponse(str(p), media_type="image/jpeg")


class MockPersonEventBody(BaseModel):
    sub_state_hint: str = Field(default="patrol", description="guard | patrol | spin")
    confidence: float = 0.85
    pose_x: float = 0.0
    pose_y: float = 0.0
    pose_yaw: float = 0.0
    floor: str = "1F"


@app.post("/api/security/dev/mock-person-event")
def api_security_dev_mock_person_event(
    body: MockPersonEventBody,
    authorization: Optional[str] = Header(None),
) -> dict[str, object]:
    """P1c Mock：模拟车端 security_person_event（需已 enter 巡逻）。"""
    _security_auth(authorization)
    if not mock_vision_enabled() and not mock_vehicle_enabled():
        raise HTTPException(
            status_code=400,
            detail="请设置 SECURITY_MOCK_VISION=1 或关闭 MQTT（mock 车端）",
        )
    data = {
        "msg_type": "security_person_event",
        "sub_state_hint": body.sub_state_hint,
        "confidence": body.confidence,
        "pose_x": body.pose_x,
        "pose_y": body.pose_y,
        "pose_yaw": body.pose_yaw,
        "floor": body.floor,
        "patrol_epoch": get_patrol_service().state.patrol_epoch,
    }
    get_patrol_service().on_security_person_event(data)
    return {"message": "mock person_event 已注入", "patrol": get_patrol_service().status_dict()}

