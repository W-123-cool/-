#!/usr/bin/env bash
# 修复 scripts 目录下 .sh 的 Windows 换行 (CRLF)，避免 pipefa / $'\r' 报错
# 用法: bash fix_scripts_crlf.sh
DIR="$(cd "$(dirname "$0")" && pwd)"
n=0
for f in "${DIR}"/*.sh; do
  [[ -f "${f}" ]] || continue
  if grep -q $'\r' "${f}" 2>/dev/null; then
    if sed -i 's/\r$//' "${f}" 2>/dev/null; then
      :
    else
      tr -d '\r' < "${f}" > "${f}.lf" && mv "${f}.lf" "${f}"
    fi
    echo "fixed: $(basename "${f}")"
    n=$((n + 1))
  fi
done
echo "完成，共修复 ${n} 个文件"
