"""复用 aichat 底盘控车，不修改原 aichat.py。"""
from __future__ import annotations

import importlib.util
import os
from typing import Any


def load_aichat():
    rksdk = os.path.expanduser(os.environ.get("AI_CAR_RKSDK", "~/Desktop/ai_app/RKSDK"))
    llm_dir = os.path.expanduser(os.environ.get("AI_CAR_LLM_DIR", f"{rksdk}/test_rkllm_run"))
    path = os.path.join(llm_dir, "aichat.py")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"未找到 aichat.py: {path}")
    spec = importlib.util.spec_from_file_location("aichat", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def is_motion_command(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if any(k in t for k in ("停止", "停下", "别动", "停车")) or t == "停":
        return True
    keys = ("前进", "后退", "左转", "右转", "米", "度", "走", "开", "转", "移", "动")
    return any(k in t for k in keys)


def process_motion_turn(
    aichat: Any,
    user_input: str,
    host: str,
    path: str,
    car_cmd: str,
    warmed: bool,
) -> bool:
    """委托 voice_to_ai_car.process_turn（若可用）。"""
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    scripts_dir = os.path.join(os.path.dirname(scripts_dir), "scripts")
    vac_path = os.path.join(scripts_dir, "voice_to_ai_car.py")
    if os.path.isfile(vac_path):
        spec = importlib.util.spec_from_file_location("voice_to_ai_car", vac_path)
        vac = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vac)
        return vac.process_turn(aichat, user_input, host, path, car_cmd, warmed)

    sec_per_meter = float(os.environ.get("AI_CAR_SEC_PER_METER", "1.0"))
    sec_per_turn90 = float(os.environ.get("AI_CAR_SEC_PER_TURN90", "4.0"))
    if hasattr(aichat, "process_user_turn"):
        return aichat.process_user_turn(
            user_input,
            host=host,
            path=path,
            car_cmd_path=car_cmd,
            yes=True,
            no_warmup=False,
            warmed=warmed,
            sec_per_meter=sec_per_meter,
            sec_per_turn90=sec_per_turn90,
        )
    return warmed
