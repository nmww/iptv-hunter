#!/usr/bin/env bash
# run_all.sh — 一键执行完整流水线（本地或 CI 通用）
# 顺序：发现 → 淘汰 → 抓取 → 去重 → 验证 → 生成 → 推送
# 用法：
#   bash scripts/run_all.sh                 # 跑全量
#   bash scripts/run_all.sh --max 100       # 验证阶段最多 100 条（省时）
#   DRY=1 bash scripts/run_all.sh           # 各阶段 dry-run（不写文件/不推送）
set -euo pipefail
cd "$(dirname "$0")/.."

DRY_FLAG=""
if [ "${DRY:-0}" = "1" ]; then DRY_FLAG="--dry-run"; fi
MAX_FLAG=""
if [ "${1:-}" = "--max" ]; then MAX_FLAG="--max ${2:-0}"; fi

echo "==> [1/7] discover"
python3 scripts/discover_sources.py $DRY_FLAG
echo "==> [2/7] retire"
python3 scripts/retire_sources.py $DRY_FLAG
echo "==> [3/7] fetch"
python3 scripts/fetch_sources.py $DRY_FLAG
echo "==> [4/7] deduplicate"
python3 scripts/deduplicate.py $DRY_FLAG
echo "==> [5/7] validate"
python3 scripts/validate_streams.py $DRY_FLAG $MAX_FLAG
echo "==> [6/7] generate"
python3 scripts/generate_m3u.py $DRY_FLAG
if [ -z "$DRY_FLAG" ]; then
  echo "==> [7/7] push"
  if [ -n "${GITEE_USE_API:-}" ]; then
    # CI 场景：代码在 GitHub、产物落 Gitee，用 API 更新文件（幂等、无历史冲突）
    python3 scripts/push_to_gitee_api.py
  else
    # 本地场景：直接 git push 回 Gitee 仓库
    chmod +x scripts/push_to_gitee.sh
    bash scripts/push_to_gitee.sh
  fi
fi
echo "完成。订阅地址见 README。"
