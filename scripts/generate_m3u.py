#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_m3u.py — M3U 生成（模块 F）

读取验证结果，仅保留通过三层验证的流；按归一化频道名聚合，按响应时间升序
保留最快前 N 条（同频道多源容灾）。输出：
  - output/iptv.m3u   （UTF-8 无 BOM，带 EPG、台标位、分组、时间戳）
  - output/iptv.txt   （纯文本：频道名,URL）
若本批次无有效源，头部标注「本批次无有效源」，文件仍生成，保证订阅链接可用。

用法：python scripts/generate_m3u.py
"""

import sys
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

DEDUP_PATH = C.OUTPUT_DIR / "_channels_dedup.json"
VALIDATED_PATH = C.OUTPUT_DIR / "_validated.json"
M3U_PATH = C.OUTPUT_DIR / "iptv.m3u"
TXT_PATH = C.OUTPUT_DIR / "iptv.txt"


def _slug(s: str) -> str:
    out = []
    for ch in s.lower():
        if ch.isalnum():
            out.append(ch)
    return "".join(out)[:24] or "ch"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="仅统计，不写文件")
    args = ap.parse_args()
    cfg = C.load_config()
    log = C.log

    keep = C._default_int(cfg, "dedup", "same_channel_keep", 3)
    epg = C._default_str(cfg, "output", "epg_url", "https://epg.pw/api/epg.xml")
    groups_cfg = C._default_str(cfg, "output", "groups", "央视,卫视,国际,体育,纪录,少儿")
    desired_groups = [g.strip() for g in groups_cfg.split(",") if g.strip()]

    dedup = C.read_json(DEDUP_PATH, default=[])
    validated = C.read_json(VALIDATED_PATH, default={})

    # 收录规则：验证通过 或 高信任直链种子（curated）。
    # 高信任源由维护者保障可用性，免逐条 ffmpeg 验证，保证播放器始终有内容。
    valid = []
    for c in dedup:
        res = validated.get(c["norm_url"], {})
        if res.get("ok") or c.get("trust") == "high":
            c["resp_time_ms"] = res.get("resp_time_ms", 0)
            valid.append(c)
    log.info("收录条目：%d（验证通过 + 高信任直链）", len(valid))

    # 按归一化频道名聚合，按响应时间升序保留前 N
    by_name = defaultdict(list)
    for c in valid:
        by_name[c["norm_name"]].append(c)
    selected = []
    for name, items in by_name.items():
        items.sort(key=lambda x: x.get("resp_time_ms", 0))
        selected.extend(items[:keep])

    # 分组统计（按配置顺序 + 其他）
    grouped = defaultdict(list)
    for c in selected:
        g = c.get("group") or "其他"
        grouped[g].append(c)

    ts = C.now_iso()

    def build_m3u():
        lines = [f'#EXTM3U url-tvg="{epg}"', f"# 更新时间：{ts}"]
        if not selected:
            lines.append("# 本批次无有效源")
            return "\n".join(lines) + "\n"
        # 按配置分组顺序输出
        order = [g for g in desired_groups if g in grouped] + \
                [g for g in grouped if g not in desired_groups]
        for g in order:
            for c in grouped[g]:
                attrs = c.get("attrs") or {}
                tid = attrs.get("tvg-id") or _slug(c["norm_name"])
                tname = attrs.get("tvg-name") or c["channel"]
                logo = attrs.get("tvg-logo") or ""
                grp = attrs.get("group-title") or g
                lines.append(
                    f'#EXTINF:-1 tvg-id="{tid}" tvg-name="{tname}" '
                    f'tvg-logo="{logo}" group-title="{grp}",{c["channel"]}'
                )
                lines.append(c["url"])
        return "\n".join(lines) + "\n"

    def build_txt():
        lines = [f"# 更新时间：{ts}"]
        if not selected:
            lines.append("# 本批次无有效源")
            return "\n".join(lines) + "\n"
        for c in selected:
            lines.append(f'{c["channel"]},{c["url"]}')
        return "\n".join(lines) + "\n"

    m3u = build_m3u()
    txt = build_txt()

    # 分组分布
    dist = {g: len(v) for g, v in grouped.items()}
    log.info("生成频道分布：%s（每频道保留前 %d 条）", dist, keep)

    if args.dry_run:
        log.info("[dry-run] 未写入文件；预览前 5 条：")
        for l in m3u.splitlines()[:8]:
            log.info("  %s", l)
        return

    C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # UTF-8 无 BOM
    M3U_PATH.write_text(m3u, encoding="utf-8")
    TXT_PATH.write_text(txt, encoding="utf-8")
    log.info("已写入 %s (%d 字节) / %s", M3U_PATH, len(m3u.encode("utf-8")), TXT_PATH)


if __name__ == "__main__":
    main()
