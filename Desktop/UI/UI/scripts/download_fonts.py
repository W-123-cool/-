"""
下载开源中文字体：写入仓库 assets/fonts，并复制到 user_client、courier_client
的 assets/fonts，供 Kivy 客户端与 Buildozer 打包。
"""
from __future__ import annotations

import os
import shutil
import sys
import urllib.request

# Noto Sans CJK 简体（notofonts/noto-cjk，OFL；单文件约 16MB）
FONT_URL = (
    "https://raw.githubusercontent.com/notofonts/noto-cjk/main/"
    "Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf"
)
FONT_FILENAME = "NotoSansCJKsc-Regular.otf"


def project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def main() -> int:
    root = project_root()
    fonts_dir = os.path.join(root, "assets", "fonts")
    os.makedirs(fonts_dir, exist_ok=True)
    dest = os.path.join(fonts_dir, FONT_FILENAME)

    if not (os.path.isfile(dest) and os.path.getsize(dest) > 100_000):
        print(f"正在下载: {FONT_URL}")
        print(f"保存到: {dest}")
        try:
            urllib.request.urlretrieve(FONT_URL, dest)
        except Exception as e:
            print(f"下载失败: {e}", file=sys.stderr)
            print(
                "可手动将 NotoSansCJKsc-Regular.otf 放到 assets/fonts/、"
                "user_client/assets/fonts/、courier_client/assets/fonts/。",
                file=sys.stderr,
            )
            return 1
        print("下载完成。")
    else:
        print(f"已存在字体: {dest}")

    uc_dir = os.path.join(root, "user_client", "assets", "fonts")
    os.makedirs(uc_dir, exist_ok=True)
    uc_dest = os.path.join(uc_dir, FONT_FILENAME)
    if os.path.isfile(dest):
        shutil.copy2(dest, uc_dest)
        print(f"已同步到取货端: {uc_dest}")

    cc_dir = os.path.join(root, "courier_client", "assets", "fonts")
    os.makedirs(cc_dir, exist_ok=True)
    cc_dest = os.path.join(cc_dir, FONT_FILENAME)
    if os.path.isfile(dest):
        shutil.copy2(dest, cc_dest)
        print(f"已同步到送货端: {cc_dest}")

    ob_dir = os.path.join(root, "onboard_client", "assets", "fonts")
    os.makedirs(ob_dir, exist_ok=True)
    ob_dest = os.path.join(ob_dir, FONT_FILENAME)
    if os.path.isfile(dest):
        shutil.copy2(dest, ob_dest)
        print(f"已同步到车载集成端: {ob_dest}")

    um_dir = os.path.join(root, "user_client_mobile", "assets", "fonts")
    os.makedirs(um_dir, exist_ok=True)
    um_dest = os.path.join(um_dir, FONT_FILENAME)
    if os.path.isfile(dest):
        shutil.copy2(dest, um_dest)
        print(f"已同步到取货手机端: {um_dest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
