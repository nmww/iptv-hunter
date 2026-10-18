#!/usr/bin/env bash
# push_to_gitee.sh — 推送脚本（模块 G）
# 将本次更新的产物提交并推送回 Gitee 仓库。
# 需要两个环境变量（不要写死在脚本里）：
#   GITEE_PUSH_TOKEN  私人令牌（需 projects 权限）
#   GITEE_REPO_URL    形如 https://oauth2:<TOKEN>@gitee.com/<user>/<repo>.git
# 本地手动运行示例：
#   GITEE_PUSH_TOKEN=xxxx \
#   GITEE_REPO_URL=https://oauth2:xxxx@gitee.com/markets2022/iptv-hunter.git \
#   bash scripts/push_to_gitee.sh

set -euo pipefail

cd "$(dirname "$0")/.."

git config user.name "iptv-bot"
git config user.email "bot@local"

# 仅追踪需要持久化的产物与注册表
git add output/iptv.m3u output/iptv.txt sources/registry.json sources/graveyard.json 2>/dev/null || true

# 空变更检测：无差异则跳过，避免空提交
if git diff --cached --quiet; then
  echo "无变更，跳过提交"
  exit 0
fi

git commit -m "auto: update iptv sources $(date '+%Y-%m-%d %H:%M')"
if [ -z "${GITEE_REPO_URL:-}" ]; then
  echo "错误：未设置 GITEE_REPO_URL，无法推送" >&2
  echo "提示：export GITEE_REPO_URL=https://oauth2:TOKEN@gitee.com/<user>/<repo>.git" >&2
  exit 1
fi

if ! git push "${GITEE_REPO_URL}" HEAD:master; then
  echo "推送失败，git 退出码：$?" >&2
  echo "排查：令牌权限/仓库地址/分支名(master/main)是否正确" >&2
  exit 1
fi

echo "推送成功"
