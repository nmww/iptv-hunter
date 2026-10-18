#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_sources.py — 抓取层（模块 B）

数据来源（按信任度从高到低）：
  1) sources/curated.json  —— 高信任直链种子（维护良好的公开 m3u，如 iptv-org /
     fanmingming / YueChan 等），免严格验证直接收录，是播放可用性的骨架。
  2) registry.json         —— 发现引擎每日新增的仓库源（需经三层验证）。
  3) sources/local/*.m3u    —— 用户本地静态补充。

解析时保留 #EXTINF 原始属性（tvg-id / tvg-name / tvg-logo / group-title），
让黄金源自带台标与分组；对无属性的纯文本 m3u 则回退到启发式分组。

产出：output/_channels_raw.json（供去重 / 验证消费）
用法：python scripts/fetch_sources.py
"""

import os
import re
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

REGISTRY_PATH = C.SOURCES_DIR / "registry.json"
CURATED_PATH = C.SOURCES_DIR / "curated.json"
LOCAL_DIR = C.SOURCES_DIR / "local"
RAW_PATH = C.OUTPUT_DIR / "_channels_raw.json"

_ATTR_RE = re.compile(r'(\w[\w-]*)="([^"]*)"')

# 仅允许 http/https 协议；组播/内网地址在家庭宽带下无法播放，一律丢弃
_UNPLAYABLE_RE = re.compile(
    r"^(rtp|rtsp|udp|srt|rtmp|mms|file):", re.IGNORECASE
)
_MULTICAST_RE = re.compile(r"(239\.|224\.|225\.|226\.|227\.|228\.|229\.|233\.|234\.|235\.|236\.|237\.|238\.)")
_IPV6_LITERAL_RE = re.compile(r"\[[0-9a-fA-F:]+\]")  # 裸 IPv6 字面量（内网单播，家庭网络通常不可达）


def is_playable_url(url: str) -> bool:
    """家庭宽带可播放的源判定：必须是 http/https，且非组播、非裸 IPv6 内网。"""
    if not url:
        return False
    if _UNPLAYABLE_RE.match(url):
        return False
    if not url.lower().startswith(("http://", "https://")):
        return False
    if _MULTICAST_RE.search(url):
        return False
    if _IPV6_LITERAL_RE.search(url):
        return False
    return True


def parse_m3u(text: str, source_repo: str, playlist_url: str, log) -> list[dict]:
    """解析 m3u 文本为 [{source_repo, playlist_url, channel, url, attrs, trust}]。

    attrs 保留 #EXTINF 中的 tvg-id / tvg-name / tvg-logo / group-title 等，
    供生成阶段直接复用，提升播放器里的台标与分组体验。
    """
    channels = []
    lines = text.splitlines()
    cur = None  # {"attrs": {...}, "name": "显示名"}
    for line in lines:
        line = line.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("#EXTINF"):
            attrs = {}
            for k, v in _ATTR_RE.findall(line):
                attrs[k.lower()] = v
            if "," in line:
                name = line.rsplit(",", 1)[-1].strip()
            else:
                name = attrs.get("tvg-name", "")
            cur = {"attrs": attrs, "name": name}
        elif line.startswith("#"):
            continue  # 其他注释行（EXTM3U / EXT-X-...）跳过
        else:
            url = line.strip()
            if cur and url:
                channels.append({
                    "source_repo": source_repo,
                    "playlist_url": playlist_url,
                    "channel": cur["name"],
                    "url": url,
                    "attrs": cur["attrs"],
                })
            cur = None
    return channels


def fetch_one(url: str, source_repo: str, cfg, log, trust: str = "") -> list[dict]:
    timeout = C._default_int(cfg, "fetch", "timeout", 10)
    retries = C._default_int(cfg, "fetch", "retries", 2)
    sc, body, ok = C.http_get(url, timeout=timeout, retries=retries)
    if not ok or not body:
        log.warning("抓取失败（跳过）：%s", url)
        return []
    chs = parse_m3u(body, source_repo, url, log)
    before = len(chs)
    chs = [c for c in chs if is_playable_url(c.get("url", ""))]
    dropped = before - len(chs)
    if trust:
        for c in chs:
            c["trust"] = trust
    if dropped:
        log.info("    过滤非可播源（rtp/组播/IPv6内网）：丢弃 %d 条", dropped)
    return chs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="仅统计，不写文件")
    args = ap.parse_args()
    cfg = C.load_config()
    log = C.log

    all_channels = []

    # ① 高信任直链种子（curated.json）——骨架来源
    curated = C.read_json(CURATED_PATH, default=[])
    n_curated_sources = 0
    for item in curated:
        url = item.get("url")
        name = item.get("name", url or "?")
        if not url:
            continue
        trust = item.get("trust", "high")
        chs = fetch_one(url, name, cfg, log, trust=trust)
        all_channels.extend(chs)
        n_curated_sources += 1
        log.info("  [curated] %s -> %d 条频道", name, len(chs))

    # ② 已发现源（registry）
    reg = C.read_json(REGISTRY_PATH, default=[])
    active = [r for r in reg if r.get("status") != "retired"]
    log.info("registry 中有效源 %d 条，开始抓取", len(active))
    for r in active:
        pu = r.get("playlist_url", "")
        if not pu:
            continue
        chs = fetch_one(pu, r.get("source_repo", ""), cfg, log)
        all_channels.extend(chs)
        log.info("  %s -> %d 条频道", r.get("source_repo", "?"), len(chs))

    # ③ 本地静态补充 m3u
    if LOCAL_DIR.exists():
        for f in sorted(LOCAL_DIR.glob("*.m3u*")):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                chs = parse_m3u(text, "local/" + f.name, str(f), log)
                chs = [c for c in chs if is_playable_url(c.get("url", ""))]
                all_channels.extend(chs)
                log.info("  本地 %s -> %d 条频道", f.name, len(chs))
            except Exception as e:  # noqa: BLE001
                log.warning("读取本地 m3u 失败 %s: %s", f, e)

    # 标注分组与归一化
    for c in all_channels:
        attrs = c.get("attrs") or {}
        g = attrs.get("group-title")
        if not g:
            g = C.classify_group(c["channel"])
        c["group"] = g
        c["norm_name"] = C.normalize_name(c["channel"])
        c["norm_url"] = C.normalize_url(c["url"])
        if "trust" not in c:
            c["trust"] = ""

    log.info("抓取合计频道条目：%d（其中 curated 源 %d 个）", len(all_channels),
             n_curated_sources)
    if not args.dry_run:
        C.write_json(RAW_PATH, all_channels)
        log.info("已写入 %s", RAW_PATH)
    else:
        log.info("[dry-run] 未写入文件")


if __name__ == "__main__":
    main()
