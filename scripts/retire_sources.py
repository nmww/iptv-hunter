#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retire_sources.py — 淘汰与回访（模块 E）

- 每日验证后（由 validate_streams.py 写入 consecutive_failures）将连续失败
  达到阈值的源移入 graveyard.json，并记录淘汰原因，避免误杀「暂时失效但会复活」的源。
- graveyard.json 中的源每隔 graveyard_revisit_days 天自动回访复检，复活则重新准入。
- 仓库超过 repo_stale_days 天无 push 的，标记 stale，后续发现层不再准入（准入逻辑见 discover）。

用法：python scripts/retire_sources.py
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

REGISTRY_PATH = C.SOURCES_DIR / "registry.json"
GRAVEYARD_PATH = C.SOURCES_DIR / "graveyard.json"


def _parse_iso(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="仅统计，不写文件")
    args = ap.parse_args()
    cfg = C.load_config()
    log = C.log

    threshold = C._default_int(cfg, "retire", "consecutive_failures_threshold", 7)
    revisit_days = C._default_int(cfg, "retire", "graveyard_revisit_days", 30)

    reg = C.read_json(REGISTRY_PATH, default=[])
    grave = C.read_json(GRAVEYARD_PATH, default=[])

    # 1) 淘汰：连续失败达到阈值
    kept = []
    retired = 0
    for e in reg:
        if e.get("status") == "retired":
            kept.append(e)
            continue
        if (e.get("consecutive_failures", 0) or 0) >= threshold:
            e["retired_at"] = C.now_iso()
            e["retire_reason"] = f"连续 {e['consecutive_failures']} 天验证失败"
            e["last_revisit"] = C.now_iso()
            grave.append(e)
            retired += 1
            log.info("  淘汰 %s：%s", e.get("source_repo"), e["retire_reason"])
        else:
            kept.append(e)
    reg = kept

    # 2) 回访：墓地中到期源复活重准入
    remain_grave = []
    revived = 0
    now = datetime.now(timezone.utc)
    for e in grave:
        last = _parse_iso(e.get("last_revisit", "")) or now
        days = (now - last).total_seconds() / 86400.0
        if days >= revisit_days:
            e["status"] = "discovered"
            e["consecutive_failures"] = 0
            e.pop("retired_at", None)
            e.pop("retire_reason", None)
            reg.append(e)
            revived += 1
            log.info("  复活 %s（重新准入，等待本轮验证）", e.get("source_repo"))
        else:
            remain_grave.append(e)
    grave = remain_grave

    log.info("淘汰 %d 条，复活 %d 条；registry=%d, graveyard=%d",
             retired, revived, len(reg), len(grave))

    if args.dry_run:
        log.info("[dry-run] 未写入文件")
        return

    C.write_json(REGISTRY_PATH, reg)
    C.write_json(GRAVEYARD_PATH, grave)
    log.info("registry.json / graveyard.json 已更新")


if __name__ == "__main__":
    main()
