"""Trim and export NovaJoy branding PNGs for Kivy clients."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from PIL import Image


def trim(im: Image.Image, threshold: int = 12) -> Image.Image:
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    min_x, min_y, max_x, max_y = w, h, 0, 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > 10 and (r > threshold or g > threshold or b > threshold):
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if max_x <= min_x:
        return im
    pad = max(4, int(min(w, h) * 0.02))
    box = (
        max(0, min_x - pad),
        max(0, min_y - pad),
        min(w, max_x + pad + 1),
        min(h, max_y + pad + 1),
    )
    return im.crop(box)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "assets" / "branding"
    out_dir.mkdir(parents=True, exist_ok=True)

    pairs = [
        ("有字图标.png", "novajoy_logo_full.png"),
        ("无字图标.png", "novajoy_icon.png"),
    ]
    for src_name, dst_name in pairs:
        src = root / src_name
        if not src.is_file():
            raise FileNotFoundError(src)
        im = trim(Image.open(src))
        dst = out_dir / dst_name
        im.save(dst, optimize=True)
        print(f"{dst_name}: {im.size}")

    icon = Image.open(out_dir / "novajoy_icon.png")
    for size, name in ((72, "novajoy_icon_72.png"), (96, "novajoy_icon_96.png")):
        icon.resize((size, size), Image.Resampling.LANCZOS).save(out_dir / name)
        print(f"{name}: ({size}, {size})")

    logo = Image.open(out_dir / "novajoy_logo_full.png")
    ratio = logo.width / logo.height
    for target_h, name in ((56, "novajoy_logo_header_sm.png"), (80, "novajoy_logo_header.png")):
        w = int(target_h * ratio)
        logo.resize((w, target_h), Image.Resampling.LANCZOS).save(out_dir / name)
        print(f"{name}: ({w}, {target_h})")

    # 同步到各 Kivy 客户端，便于 APK 打包离线资源
    for sub in ("user_client", "courier_client", "onboard_client"):
        dest_dir = root / sub / "assets" / "branding"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for png in out_dir.glob("*.png"):
            shutil.copy2(png, dest_dir / png.name)
        print(f"synced -> {dest_dir}")


if __name__ == "__main__":
    main()
