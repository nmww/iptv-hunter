#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discover_sources.py — 发现层（本项目灵魂，模块 A）

四条发现渠道，全部在运行时通过网络动态获取源，代码中不硬编码任何具体
IPTV 源地址（仅 seeds.json 允许 awesome-iptv 类元列表仓库作冷启动引路）：

  渠道 1：GitHub Search API 主渠道（带仓库级准入门槛 + 限流处理）
  渠道 2：递归发现（解析已准入仓库 README 中的互链，深度 ≤ 2）
  渠道 3：Awesome 元列表挖掘（从 seeds.json 出发，递归 1 层）
  渠道 4：DuckDuckGo HTML 兜底（无 key，失败静默跳过）

所有新增源写入 sources/registry.json（程序自动维护），并打印当日发现摘要。

用法：
  python scripts/discover_sources.py            # 正常发现并写入 registry
  python scripts/discover_sources.py --dry-run  # 只观察发现结果，不触碰验证/推送
"""

import os
import re
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

GITHUB_API = "https://api.github.com"
REGISTRY_PATH = C.SOURCES_DIR / "registry.json"

# 每日轮换的查询词（避免单日结果趋同）。索引由「日序号」决定。
SEARCH_QUERIES = [
    "q=iptv+in:name,description&sort=updated&order=desc&per_page=100",
    "q=iptv+m3u+stars:%3E50+pushed:%3E{30d}&sort=stars&order=desc&per_page=100",
    "q=%E7%9B%B4%E6%92%AD%E6%BA%90+in:readme&sort=updated&order=desc&per_page=100",
    "q=iptv+playlist+language:python&sort=updated&order=desc&per_page=100",
]

PLAYLIST_EXT_RE = re.compile(r"\.m3u8?$", re.IGNORECASE)
GITHUB_REPO_RE = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.IGNORECASE)
M3U_LINK_RE = re.compile(r"https?://[^\s)>\"]+\.m3u8?", re.IGNORECASE)


def _gh_headers(token: str | None) -> dict:
    hdrs = {
        "Accept": "application/vnd.github+json",
        "User-Agent": C.DEFAULT_UA,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    return hdrs


def _rate_limit_guard(resp, token: str | None, log):
    """读取限流响应头，剩余过低时 sleep 到重置（匿名额度极小，尽力而为）。"""
    if not token:
        return  # 匿名不 sleep，交给上层捕获失败
    remaining = resp.headers.get("X-RateLimit-Remaining")
    reset = resp.headers.get("X-RateLimit-Reset")
    try:
        if remaining and int(remaining) < 100 and reset:
            wait = max(0, int(reset) - int(time.time())) + 2
            wait = min(wait, 120)  # 单次等待封顶 120s，避免流水线卡死
            log.warning("GitHub 限流临近，sleep %ds 等待重置", wait)
            time.sleep(wait)
    except (ValueError, TypeError):
        pass


def github_api_get(url: str, params: dict, token: str | None, cfg, log):
    """封装 GitHub API GET，处理限流与重试，返回 (json_or_None, ok)。"""
    timeout = C._default_int(cfg, "discover", "request_timeout", 15)
    retries = C._default_int(cfg, "discover", "request_retries", 2)
    for attempt in range(retries + 1):
        try:
            import requests

            resp = requests.get(url, params=params, headers=_gh_headers(token),
                               timeout=timeout)
            _rate_limit_guard(resp, token, log)
            if resp.status_code == 200:
                return resp.json(), True
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                log.warning("GitHub 触发限流(403)，本次响应丢弃")
                return None, False
            log.warning("GitHub API %s 返回 %s: %s", url, resp.status_code,
                        resp.text[:120])
            if attempt < retries:
                time.sleep(2 + attempt)
        except Exception as e:  # noqa: BLE001
            log.warning("GitHub API 请求异常 %s: %s", url, e)
            if attempt < retries:
                time.sleep(2 + attempt)
    return None, False


def _days_since(iso_date: str) -> float:
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    except Exception:  # noqa: BLE001
        return 9999.0


def check_admission(repo: dict, cfg, log) -> tuple[bool, str]:
    """仓库级准入：stars / 近期 push / 文件树含播放列表，三者同时满足。"""
    stars = repo.get("stargazers_count", 0) or 0
    pushed = repo.get("pushed_at", "")
    days = _days_since(pushed)
    # 门槛：stars>=20，或（7 天内新建且 stars>=5）
    if days <= 7:
        if stars < 5:
            return False, f"新仓库 stars={stars}<5"
    else:
        if stars < 20:
            return False, f"stars={stars}<20"
    if days > C._default_int(cfg, "retire", "repo_stale_days", 90):
        return False, f"pushed {days:.0f}d 前，已不活跃"
    # 文件树含 .m3u/.m3u8/.txt
    owner = repo["owner"]["login"]
    name = repo["name"]
    branch = repo.get("default_branch") or "master"
    tree, ok = github_api_get(f"{GITHUB_API}/repos/{owner}/{name}/git/trees/{branch}",
                              {"recursive": "1"}, None, cfg, log) if False else \
        github_api_get(f"{GITHUB_API}/repos/{owner}/{name}/git/trees/{branch}",
                      {"recursive": "1"}, _token_for_tree(cfg), cfg, log)
    if not ok or not tree:
        return False, "无法读取文件树"
    has_playlist = any(
        "path" in t and PLAYLIST_EXT_RE.search(t["path"])
        for t in tree.get("tree", [])
    )
    if not has_playlist:
        return False, "文件树无播放列表文件"
    return True, "ok"


def _token_for_tree(cfg):
    env = C._default_str(cfg, "discover", "github_token_env", "GITHUB_TOKEN")
    return os.environ.get(env) or None


def extract_playlist_urls(repo: dict) -> list[str]:
    """从仓库默认分支文件树提取所有播放列表的 raw 直链。"""
    owner = repo["owner"]["login"]
    name = repo["name"]
    branch = repo.get("default_branch") or "master"
    tree, ok = github_api_get(f"{GITHUB_API}/repos/{owner}/{name}/git/trees/{branch}",
                              {"recursive": "1"}, _token_for_tree(C.load_config()),
                              C.load_config(), C.log)
    urls = []
    if ok and tree:
        for t in tree.get("tree", []):
            p = t.get("path", "")
            if PLAYLIST_EXT_RE.search(p) and t.get("type") == "blob":
                urls.append(f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/{p}")
    return urls


def parse_readme_links(text: str) -> tuple[set, set]:
    """从 README 文本提取 (github 仓库集合, m3u 直链集合)。"""
    repos = set()
    for m in GITHUB_REPO_RE.finditer(text):
        repos.add(f"{m.group(1)}/{m.group(2).rstrip('.')}")
    m3us = set(M3U_LINK_RE.findall(text))
    return repos, m3us


def load_registry() -> list:
    return C.read_json(REGISTRY_PATH, default=[])


def registry_contains(reg: list, playlist_url: str) -> bool:
    nu = C.normalize_url(playlist_url)
    return any(C.normalize_url(r.get("playlist_url", "")) == nu for r in reg)


def add_entry(reg: list, source_repo: str, playlist_url: str, stars: int,
              last_push: str, via: str, log) -> bool:
    if registry_contains(reg, playlist_url):
        return False
    reg.append({
        "source_repo": source_repo,
        "playlist_url": playlist_url,
        "discovered_via": via,
        "discovered_at": C.now_iso(),
        "stars": stars,
        "last_push": last_push,
        "last_verified": None,
        "consecutive_failures": 0,
        "status": "discovered",
    })
    return True


def discover_via_search(cfg, token, log, visited_repos: set, reg: list,
                        max_new: int, dry_run: bool) -> int:
    added = 0
    day_index = datetime.now().day % len(SEARCH_QUERIES)
    # 把 30 天前的时间戳注入查询（pushed:>YYYY-MM-DD）
    since = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d")
    pages = C._default_int(cfg, "discover", "max_pages_per_query", 5)
    for qi, q in enumerate(SEARCH_QUERIES):
        if added >= max_new:
            break
        # 每天从 day_index 开始轮转，避免单调
        query = SEARCH_QUERIES[(day_index + qi) % len(SEARCH_QUERIES)]
        query = query.replace("{30d}", since)
        for page in range(1, pages + 1):
            if added >= max_new:
                break
            params = {"page": page}
            data, ok = github_api_get(f"{GITHUB_API}/search/repositories",
                                     {**_split_q(query), **params}, token, cfg, log)
            if not ok or not data:
                break
            for repo in data.get("items", []):
                full = f"{repo['owner']['login']}/{repo['name']}"
                if full in visited_repos:
                    continue
                visited_repos.add(full)
                adm, reason = check_admission(repo, cfg, log)
                if not adm:
                    log.debug("跳过 %s: %s", full, reason)
                    continue
                for pu in extract_playlist_urls(repo):
                    if add_entry(reg, full, pu, repo.get("stargazers_count", 0),
                                repo.get("pushed_at", ""), "github_search", log):
                        added += 1
                        if added >= max_new:
                            break
            # GitHub search 单页满 100 才可能有下一页
            if len(data.get("items", [])) < 100:
                break
    return added


def _split_q(query: str) -> dict:
    """把 'q=...&sort=...&order=...&per_page=...' 拆成请求参数 dict。"""
    out = {}
    for kv in query.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k] = v
        else:
            out[kv] = ""
    return out


def discover_recursive(cfg, token, log, start_repos: set, visited_repos: set,
                       reg: list, max_new: int, depth: int = 2,
                       via: str = "recursive") -> int:
    added = 0
    queue = [(r, 0) for r in start_repos if r not in visited_repos]
    while queue and added < max_new:
        repo_full, d = queue.pop(0)
        if repo_full in visited_repos:
            continue
        visited_repos.add(repo_full)
        if d >= depth:
            continue
        owner, _, name = repo_full.partition("/")
        # 取 README 内容
        for branch in ("master", "main"):
            url = f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/README.md"
            sc, body, ok = C.http_get(url, timeout=15, retries=2)
            if ok and body:
                repos, m3us = parse_readme_links(body)
                # m3u 直链直接作为新源（仍过准入简化为：直接收录，真实可播放性由验证层把关）
                for mu in m3us:
                    if add_entry(reg, repo_full, mu, 0, "", via, log):
                        added += 1
                # 互链仓库递归
                for r in repos:
                    if r not in visited_repos:
                        queue.append((r, d + 1))
                break
    return added


def discover_ddg(cfg, token, log, visited_repos: set, reg: list,
                 max_new: int) -> int:
    """渠道 4：DuckDuckGo HTML 兜底，任何失败静默跳过。"""
    added = 0
    try:
        queries = [
            "iptv m3u github 自动更新",
            "%E7%9B%B4%E6%92%AD%E6%BA%90%20%E6%AF%8F%E6%97%A5%E6%9B%B4%E6%96%B0%20site:github.com",
        ]
        for q in queries:
            if added >= max_new:
                break
            sc, body, ok = C.http_get(f"https://html.duckduckgo.com/html/?q={q}",
                                     timeout=15, retries=1)
            if not ok or not body:
                continue
            repos = set(m.group(1) + "/" + m.group(2).rstrip(".")
                        for m in GITHUB_REPO_RE.finditer(body))
            for r in repos:
                if r in visited_repos or added >= max_new:
                    continue
                visited_repos.add(r)
                owner, _, name = r.partition("/")
                # 走一遍简化准入：能取到文件树且含播放列表才收录
                sample = {"owner": {"login": owner}, "name": name,
                          "default_branch": "master", "stargazers_count": 0,
                          "pushed_at": datetime.now(timezone.utc).isoformat()}
                adm, _ = check_admission(sample, cfg, log)
                if not adm:
                    continue
                for pu in extract_playlist_urls(sample):
                    if add_entry(reg, r, pu, 0, "", "search_engine", log):
                        added += 1
    except Exception as e:  # noqa: BLE001
        log.warning("DuckDuckGo 渠道异常（已忽略）：%s", e)
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="只观察发现结果，不执行验证与推送")
    args = ap.parse_args()

    cfg = C.load_config()
    env = C._default_str(cfg, "discover", "github_token_env", "GITHUB_TOKEN")
    token = os.environ.get(env) or None
    max_new = C._default_int(cfg, "discover", "max_new_repos_per_day", 50)
    log = C.log
    if token:
        log.info("检测到 %s，GitHub 发现配额 5000/h", env)
    else:
        log.warning("未检测到 %s，使用匿名(60/h)，发现量受限——建议配置令牌", env)

    reg = load_registry()
    before = len(reg)
    visited_repos: set = set(r.get("source_repo", "") for r in reg)

    log.info("=== 渠道 1：GitHub Search API ===")
    n1 = discover_via_search(cfg, token, log, visited_repos, reg, max_new, args.dry_run)

    log.info("=== 渠道 3：Awesome 元列表挖掘（seeds.json）===")
    seeds = C.read_json(C.SOURCES_DIR / "seeds.json", default=[])
    meta_repos = {s.get("repo", "") for s in seeds if s.get("repo")}
    n3 = discover_recursive(cfg, token, log, meta_repos, visited_repos, reg,
                            max_new, depth=1, via="awesome")

    log.info("=== 渠道 2：递归发现（已准入仓库 README 互链）===")
    admitted = {r.get("source_repo", "") for r in reg if r.get("source_repo")}
    n2 = discover_recursive(cfg, token, log, admitted, visited_repos, reg,
                            max_new, depth=2, via="recursive")

    log.info("=== 渠道 4：DuckDuckGo HTML 兜底 ===")
    n4 = discover_ddg(cfg, token, log, visited_repos, reg, max_new)

    total_added = n1 + n2 + n3 + n4
    log.info("本次新增源条目：%d（search=%d, awesome=%d, recursive=%d, ddg=%d）",
             total_added, n1, n3, n2, n4)

    if not args.dry_run:
        C.write_json(REGISTRY_PATH, reg)
        log.info("registry.json 已更新：%d → %d 条", before, len(reg))
    else:
        log.info("[dry-run] 未写入 registry.json；预览前 20 条新增源：")
        for r in reg[before:]:
            log.info("  + %s  via=%s  %s", r["source_repo"], r["discovered_via"],
                     r["playlist_url"])

    log.info("发现层完成。下一步：retire → fetch → deduplicate → validate → generate → push")


if __name__ == "__main__":
    main()
