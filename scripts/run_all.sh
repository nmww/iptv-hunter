#!/usr/bin/env bash
# run_all.sh — 一键执行完整流水线（本地或 CI 通用）
# 顺序：发现 → 淘汰 → 抓取 → 去重 → 验证 → 生成
# 注：产物（output/iptv.m3u 等）由调用方负责落库——
#     GitHub Actions 里由 workflow 的「Commit playlist back to GitHub」步骤 git push 回 main；
#     本地手动跑则用 git 自行提交。本脚本不再直接推送（已改为 GitHub 单一平台）。
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

echo "==> [1/6] discover"
python3 scripts/discover_sources.py $DRY_FLAG
echo "==> [2/6] retire"
python3 scripts/retire_sources.py $DRY_FLAG
echo "==> [3/6] fetch"
python3 scripts/fetch_sources.py $DRY_FLAG
echo "==> [4/6] deduplicate"
python3 scripts/deduplicate.py $DRY_FLAG
echo "==> [5/6] validate"
python3 scripts/validate_streams.py $DRY_FLAG $MAX_FLAG
echo "==> [6/6] generate"
python3 scripts/generate_m3u.py $DRY_FLAG
echo "完成。产物已生成于 output/，由上层（Actions 或手动 git）负责提交。"
