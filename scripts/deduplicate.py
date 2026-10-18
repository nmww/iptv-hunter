#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deduplicate.py — 去重引擎（模块 C）

两步归一化 + 双重去重：
  1) URL 归一化（小写 scheme/host、去默认端口、去时间戳类 query、排序 query）
  2) 频道名归一化（全半角、去空格符号、繁简、小写）
  3) 先按归一化 URL 去重（同一资源不同参数顺序视为重复）
  4) 不同 URL 但同一归一化频道名 → 全部保留为候选（同频道多源容灾），
     最终「每频道保留最快前 N 条」由 generate_m3u 按验证响应时间裁剪。

产出：output/_channels_dedup.json
用法：python scripts/deduplicate.py
"""

import sys
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

RAW_PATH = C.OUTPUT_DIR / "_channels_raw.json"
DEDUP_PATH = C.OUTPUT_DIR / "_channels_dedup.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="仅统计，不写文件")
    args = ap.parse_args()
    log = C.log

    raw = C.read_json(RAW_PATH, default=[])
    if not raw:
        log.warning("未找到 %s，请先运行 fetch_sources.py", RAW_PATH)
        return

    # 第一步：按归一化 URL 去重，保留首次出现
    seen_url = set()
    by_name = defaultdict(list)
    unique = []
    for c in raw:
        nu = c.get("norm_url") or C.normalize_url(c.get("url", ""))
        if nu in seen_url:
            continue
        seen_url.add(nu)
        c["norm_url"] = nu
        c["norm_name"] = c.get("norm_name") or C.normalize_name(c.get("channel", ""))
        unique.append(c)
        by_name[c["norm_name"]].append(c)

    dup_url = len(raw) - len(unique)
    # 统计同名多源
    multi = {k: len(v) for k, v in by_name.items() if len(v) > 1}

    log.info("原始条目 %d，按 URL 去重后 %d（剔除重复 URL %d）", len(raw),
             len(unique), dup_url)
    log.info("同名多源频道数：%d（将在生成阶段按响应时间保留前 N 条）", len(multi))

    if not args.dry_run:
        C.write_json(DEDUP_PATH, unique)
        log.info("已写入 %s", DEDUP_PATH)
    else:
        log.info("[dry-run] 未写入文件")


if __name__ == "__main__":
    main()
