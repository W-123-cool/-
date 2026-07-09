#!/usr/bin/env python3
"""兼容旧名：请使用 voice_to_ai_car.py"""
import runpy
import sys
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("voice_to_ai_car.py")), run_name="__main__")
