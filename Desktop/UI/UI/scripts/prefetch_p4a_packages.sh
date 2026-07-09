#!/usr/bin/env bash
# 预下载 python-for-android 构建时易失败的源码包（网络不稳定时）
set -euo pipefail

STORAGE="${1:-$HOME/novajoy-ui-build/user_client_mobile/.buildozer/android/platform/build-arm64-v8a_armeabi-v7a}"
OPENSSL_DIR="$STORAGE/packages/openssl"
OPENSSL_FILE="$OPENSSL_DIR/openssl-3.3.1.tar.gz"
OPENSSL_URL="https://github.com/openssl/openssl/releases/download/openssl-3.3.1/openssl-3.3.1.tar.gz"

mkdir -p "$OPENSSL_DIR"

if [[ -f "$OPENSSL_FILE" ]] && [[ $(stat -c%s "$OPENSSL_FILE" 2>/dev/null || stat -f%z "$OPENSSL_FILE") -gt 1000000 ]]; then
  echo "openssl 已存在: $OPENSSL_FILE"
  exit 0
fi

echo "下载 openssl-3.3.1.tar.gz ..."
curl -fL --retry 8 --retry-delay 5 --connect-timeout 30 \
  -o "$OPENSSL_FILE.part" "$OPENSSL_URL"
mv "$OPENSSL_FILE.part" "$OPENSSL_FILE"
touch "$OPENSSL_DIR/.mark-openssl-3.3.1.tar.gz"
ls -lh "$OPENSSL_FILE"
echo "完成（已写入 .mark 文件，buildozer 将跳过重复下载）"
