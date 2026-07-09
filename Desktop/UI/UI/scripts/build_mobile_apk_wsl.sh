#!/usr/bin/env bash
# 在 WSL 的 Linux 文件系统内打包手机 APK（勿在 /mnt/d/ 或 /mnt/c/ 上直接 buildozer）。
#
# 用法（Ubuntu bash 内）:
#   cd /mnt/d/cd/RTTH-update/UI
#   bash scripts/build_mobile_apk_wsl.sh
#
# 可选环境变量:
#   NOVAJOY_SRC  Windows 挂载上的 UI 根目录（默认自动检测）
#   NOVAJOY_BUILD  Linux 内构建目录（默认 ~/novajoy-ui-build）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UI_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC="${NOVAJOY_SRC:-$UI_ROOT}"
BUILD="${NOVAJOY_BUILD:-$HOME/novajoy-ui-build}"

# Windows 编辑器可能写入 CRLF，WSL bash 需要 LF
sed -i 's/\r$//' "$UI_ROOT/scripts/"*.sh 2>/dev/null || true

if [[ "$SRC" == /mnt/* ]]; then
  echo "==> 源目录在 Windows 盘: $SRC"
  echo "    将复制到 Linux 目录再打包（Buildozer 不能在 /mnt/c|d 上可靠构建）"
else
  echo "==> 源目录: $SRC"
fi

echo "==> 构建目录: $BUILD"

mkdir -p "$BUILD"
rsync -a --delete \
  --exclude '.buildozer' \
  --exclude 'bin/*.apk' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude '.git' \
  "$SRC/" "$BUILD/"

# 确保字体存在
if [[ ! -f "$BUILD/user_client_mobile/assets/fonts/NotoSansCJKsc-Regular.otf" ]]; then
  if [[ -f "$BUILD/user_client/assets/fonts/NotoSansCJKsc-Regular.otf" ]]; then
    mkdir -p "$BUILD/user_client_mobile/assets/fonts"
    cp "$BUILD/user_client/assets/fonts/NotoSansCJKsc-Regular.otf" \
      "$BUILD/user_client_mobile/assets/fonts/"
  else
    echo "缺少中文字体，正在运行 download_fonts.py ..."
    (cd "$BUILD" && python3 scripts/download_fonts.py)
  fi
fi

# 预下载易因网络中断失败的依赖（openssl 等）
bash "$BUILD/scripts/prefetch_p4a_packages.sh" \
  "$BUILD/user_client_mobile/.buildozer/android/platform/build-arm64-v8a_armeabi-v7a" \
  || bash "$BUILD/scripts/prefetch_p4a_packages.sh" \
  "$BUILD/user_client_mobile/.buildozer/android/platform/build-arm64-v8a"

cd "$BUILD/user_client_mobile"

echo "==> 开始 buildozer android debug（首次可能 30~90 分钟）..."
buildozer android debug

mkdir -p "$SRC/user_client_mobile/bin"
shopt -s nullglob
apks=(bin/*.apk)
if ((${#apks[@]})); then
  cp -v bin/*.apk "$SRC/user_client_mobile/bin/"
  echo ""
  echo "==> 完成。APK 已复制到:"
  ls -lh "$SRC/user_client_mobile/bin/"*.apk
else
  echo "未找到 bin/*.apk，请向上滚动查看 buildozer 报错。" >&2
  exit 1
fi
