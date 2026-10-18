#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_sources.py — 抓取层（模块 B）

遍历 registry.json 中所有非淘汰源的 playlist_url，下载 m3u 内容并解析出
「频道名 + 流地址」对；同时读取 sources/local/ 下本地 m3u 一并合入。
单源超时 10s、重试 2 次，某源全部失效时记录日志并跳过，不中断整体流程。

产出：output/_channels_raw.json（供去重 / 验证消费）
用法：python scripts/fetch_sources.py
"""

import os
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

REGISTRY_PATH = C.SOURCES_DIR / "registry.json"
LOCAL_DIR = C.SOURCES_DIR / "local"
RAW_PATH = C.OUTPUT_DIR / "_channels_raw.json"


def parse_m3u(text: str, source_repo: str, playlist_url: str, log) -> list[dict]:
    """解析 m3u 文本为 [{source_repo, playlist_url, channel, url}]。"""
    channels = []
    lines = text.splitlines()
    cur_name = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            # 频道名取最后一个逗号后的文本
            if "," in line:
                cur_name = line.rsplit(",", 1)[-1].strip()
            else:
                cur_name = None
        elif line.startswith("#"):
            continue  # 其他注释行（EXTM3U / EXT-X-...）跳过
        else:
            url = line.strip()
            if cur_name and url:
                channels.append({
                    "source_repo": source_repo,
                    "playlist_url": playlist_url,
                    "channel": cur_name,
                    "url": url,
                })
            cur_name = None
    return channels


def fetch_one(url: str, source_repo: str, cfg, log) -> list[dict]:
    timeout = C._default_int(cfg, "fetch", "timeout", 10)
    retries = C._default_int(cfg, "fetch", "retries", 2)
    sc, body, ok = C.http_get(url, timeout=timeout, retries=retries)
    if not ok or not body:
        log.warning("抓取失败（跳过）：%s", url)
        return []
    return parse_m3u(body, source_repo, url, log)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="仅统计，不写文件")
    args = ap.parse_args()
    cfg = C.load_config()
    log = C.log

    reg = C.read_json(REGISTRY_PATH, default=[])
    active = [r for r in reg if r.get("status") != "retired"]
    log.info("registry 中有效源 %d 条，开始抓取", len(active))

    all_channels = []
    for r in active:
        pu = r.get("playlist_url", "")
        if not pu:
            continue
        chs = fetch_one(pu, r.get("source_repo", ""), cfg, log)
        all_channels.extend(chs)
        log.info("  %s -> %d 条频道", r.get("source_repo", "?"), len(chs))

    # 合入本地静态补充 m3u
    if LOCAL_DIR.exists():
        for f in sorted(LOCAL_DIR.glob("*.m3u*")):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                chs = parse_m3u(text, "local/" + f.name, str(f), log)
                all_channels.extend(chs)
                log.info("  本地 %s -> %d 条频道", f.name, len(chs))
            except Exception as e:  # noqa: BLE001
                log.warning("读取本地 m3u 失败 %s: %s", f, e)

    # 标注分组（启发式，供生成阶段归类）
    for c in all_channels:
        c["group"] = C.classify_group(c["channel"])
        c["norm_name"] = C.normalize_name(c["channel"])
        c["norm_url"] = C.normalize_url(c["url"])

    log.info("抓取合计频道条目：%d", len(all_channels))
    if not args.dry_run:
        C.write_json(RAW_PATH, all_channels)
        log.info("已写入 %s", RAW_PATH)
    else:
        log.info("[dry-run] 未写入文件")


if __name__ == "__main__":
    main()
