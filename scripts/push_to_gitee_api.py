#!/usr/bin/env python3
# push_to_gitee_api.py — 通过 Gitee contents API 更新产物文件（幂等、无 git 历史冲突）
#
# 与 push_to_gitee.sh（git push，适合本地）不同，本脚本用 Gitee OpenAPI 直接
# 更新指定文件内容。在 GitHub Actions 等「代码在 A 仓库、产物要落到 B 仓库」的
# 场景下最稳妥：不依赖两仓库 git 历史一致，先 GET 拿到 blob sha 再 PUT 更新，
# 文件不存在则自动创建。
#
# 需要环境变量（绝不写死）：
#   GITEE_PAT    Gitee 私人令牌（必填；缺失则跳过并退出 0，便于本地 dry-run）
#   GITEE_REPO   形如 owner/repo（默认 markets2022/iptv-hunter）
#   GITEE_BRANCH 目标分支（默认 master）
#
# 用法：
#   GITEE_PAT=xxxx python3 scripts/push_to_gitee_api.py

import os
import sys
import base64
import json

import requests

API_BASE = "https://gitee.com/api/v5"
TOKEN = os.environ.get("GITEE_PAT") or os.environ.get("GITEE_PUSH_TOKEN")
REPO = os.environ.get("GITEE_REPO", "markets2022/iptv-hunter")
BRANCH = os.environ.get("GITEE_BRANCH", "master")

# 需要持久化的产物（与 push_to_gitee.sh 保持一致）
FILES = [
    "output/iptv.m3u",
    "output/iptv.txt",
    "sources/registry.json",
    "sources/graveyard.json",
]

UA = "iptv-hunter/1.0 (+https://gitee.com/markets2022/iptv-hunter)"


def update_file(path: str) -> None:
    url = f"{API_BASE}/repos/{REPO}/contents/{path}"
    headers = {"User-Agent": UA}
    params = {"access_token": TOKEN, "branch": BRANCH}

    # 1) 取现有 sha（不存在则创建）
    r = requests.get(url, params=params, headers=headers, timeout=20)
    sha = None
    if r.status_code == 200:
        sha = r.json().get("sha")
    elif r.status_code not in (404,):
        # 其它错误（如限流 403）打印后跳过，不致命
        print(f"  [skip] {path}: GET 失败 {r.status_code} {r.text[:120]}")
        return

    with open(path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("ascii")

    data = {
        "message": f"auto: update {path} via CI",
        "content": content_b64,
        "branch": BRANCH,
    }
    if sha:
        data["sha"] = sha

    rr = requests.put(url, params={"access_token": TOKEN}, json=data,
                     headers=headers, timeout=30)
    if rr.status_code in (200, 201):
        commit = rr.json().get("commit", {}).get("sha", "")[:8]
        print(f"  [ok]   {path}  -> commit {commit}")
    else:
        print(f"  [fail] {path}: {rr.status_code} {rr.text[:160]}")


def main() -> int:
    if not TOKEN:
        print("GITEE_PAT 未设置，跳过 Gitee 推送（本地 dry-run 安全退出）")
        return 0

    print(f"==> 推送产物到 Gitee：{REPO} @ {BRANCH}")
    for f in FILES:
        if not os.path.exists(f):
            print(f"  [skip] {f}: 本地不存在")
            continue
        try:
            update_file(f)
        except Exception as e:  # noqa: BLE001
            print(f"  [error] {f}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
