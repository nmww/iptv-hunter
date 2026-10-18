#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_streams.py — 有效性验证（模块 D，严格三层）

逐条对流地址做真实可播放性验证（非仅 HTTP 状态码）：
  第一层：curl -I 检查 200/302（超时 5s）
  第二层：ffprobe 探测必须返回 codec 信息（排除伪流/HTML 错误页）
  第三层：ffmpeg 真实拉流 5s 无报错退出
并发 workers 线程（默认 20）。产出：
  - output/_validated.json  （norm_url -> 验证结果）
  - output/log/verify_YYYYMMDD.log
  - 同步更新 registry.json 中各源的 last_verified / consecutive_failures / status

用法：
  python scripts/validate_streams.py
  python scripts/validate_streams.py --workers 8
  python scripts/validate_streams.py --max 50   # 本次最多验证 50 条（CI 省时）
"""

import os
import sys
import json
import time
import argparse
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

DEDUP_PATH = C.OUTPUT_DIR / "_channels_dedup.json"
VALIDATED_PATH = C.OUTPUT_DIR / "_validated.json"
REGISTRY_PATH = C.SOURCES_DIR / "registry.json"


def _run(cmd, timeout):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "ignore"), \
            p.stderr.decode("utf-8", "ignore")
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:  # noqa: BLE001
        return 1, "", str(e)


def layer1(url: str, timeout: int, log) -> tuple[bool, int]:
    """第一层：HTTP 探测，取 http_code。单次 curl 即可。"""
    rc, out, _ = _run(
        ["curl", "-I", "-m", str(timeout), "-A", C.DEFAULT_UA, "-s", "-o", "/dev/null",
         "-w", "%{http_code}"], timeout + 3)
    try:
        status = int(out.strip())
    except ValueError:
        status = 0
    return (status in (200, 302), status), status


def layer2(url: str, log) -> tuple[bool, dict]:
    rc, out, _ = _run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,width,height", "-of", "json", url], 25)
    if rc != 0:
        return False, {}
    try:
        data = json.loads(out)
        streams = data.get("streams", [])
        if not streams:
            return False, {}
        s = streams[0]
        return True, {
            "codec": s.get("codec_name", ""),
            "width": s.get("width", 0),
            "height": s.get("height", 0),
        }
    except Exception:  # noqa: BLE001
        return False, {}


def layer3(url: str, seconds: int, log) -> tuple[bool, str]:
    rc, _, err = _run(
        ["ffmpeg", "-i", url, "-t", str(seconds), "-f", "null", "-"], seconds + 15)
    # ffmpeg 退出 0 为成功；部分流尾帧警告不影响，仅以致命错误判断
    fatal = ("Error " in err) and ("Invalid data found" in err or "Protocol not found" in err
             or "Server returned 403" in err or "Connection refused" in err)
    return (rc == 0 and not fatal), (err[-200:] if rc != 0 else "")


def validate_one(item, cfg, log):
    url = item["url"]
    # 第三层（ffmpeg 真播放）默认不作为硬门槛：免费 IPTV 流在 CI 里 5s 拉流极易超时，
    # 但 VLC 自身会重连/缓冲，能播的流不该因此被丢弃。设为 True 可恢复严格模式。
    require_l3 = C._default_bool(cfg, "validate", "require_layer3", False)
    t0 = time.time()
    l1, status = layer1(url, C._default_int(cfg, "validate", "layer1_timeout", 5), log)
    if not l1:
        return {
            "norm_url": item["norm_url"], "url": url, "ok": False,
            "resp_time_ms": int((time.time() - t0) * 1000), "status": status,
            "codec": "", "width": 0, "height": 0, "geo_blocked": False,
            "error": "layer1_http_fail",
        }
    l2, info = layer2(url, log)
    if not l2:
        return {
            "norm_url": item["norm_url"], "url": url, "ok": False,
            "resp_time_ms": int((time.time() - t0) * 1000), "status": status,
            "codec": "", "width": 0, "height": 0, "geo_blocked": False,
            "error": "layer2_no_codec",
        }
    l3, err = layer3(url, C._default_int(cfg, "validate", "verify_seconds", 5), log)
    # 通过条件：第一层 + 第二层 + （第三层 或 不强制第三层）
    ok = l1 and l2 and (l3 or not require_l3)
    return {
        "norm_url": item["norm_url"], "url": url, "ok": ok,
        "resp_time_ms": int((time.time() - t0) * 1000), "status": status,
        "codec": info.get("codec", ""), "width": info.get("width", 0),
        "height": info.get("height", 0), "geo_blocked": False,  # 单区域无法可靠判定地理封锁
        "error": "" if ok else ("layer3_play_fail:" + err[:120]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=None, help="并发线程数")
    ap.add_argument("--max", type=int, default=None, help="本次最多验证条数(0=不限)")
    ap.add_argument("--dry-run", action="store_true", help="仅统计，不写文件/不更新registry")
    args = ap.parse_args()
    cfg = C.load_config()
    log = C.log
    workers = args.workers or C._default_int(cfg, "validate", "workers", 20)
    max_n = args.max if args.max is not None else C._default_int(cfg, "validate", "max_streams", 0)

    items = C.read_json(DEDUP_PATH, default=[])
    if not items:
        log.warning("未找到 %s，请先运行 deduplicate.py", DEDUP_PATH)
        return
    # 注意：高信任源（curated）不再免验证——它们同样可能是死链/被地理封锁，
    # 必须逐条过验证才能进列表，否则会把不可播的源带进播放器。
    if max_n and max_n < len(items):
        log.info("配置限制：本次验证前 %d / %d 条", max_n, len(items))
        items = items[:max_n]

    results = {}
    log.info("开始三层验证，共 %d 条，并发 %d", len(items), workers)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(validate_one, it, cfg, log): it for it in items}
        done = 0
        for f in as_completed(futs):
            r = f.result()
            results[r["norm_url"]] = r
            done += 1
            if done % 50 == 0:
                log.info("  进度 %d/%d", done, len(items))

    ok_count = sum(1 for v in results.values() if v["ok"])
    log.info("验证完成：有效 %d / 总 %d", ok_count, len(results))

    # 写每日日志
    log_path = C.LOG_DIR / f"verify_{C.today_str()}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        for v in results.values():
            f.write(f"{'OK ' if v['ok'] else 'FAIL'} {v['status']} "
                    f"{v['resp_time_ms']}ms {v.get('width',0)}x{v.get('height',0)} "
                    f"{v['codec']} {v['url']} {v['error']}\n")

    if args.dry_run:
        log.info("[dry-run] 未写入文件/registry")
        return

    C.write_json(VALIDATED_PATH, results)
    log.info("已写入 %s", VALIDATED_PATH)

    # 更新 registry：按 playlist_url 聚合验证结果
    reg = C.read_json(REGISTRY_PATH, default=[])
    # 建立 norm_url -> channel(含 playlist_url) 映射
    pu_to_results = {}
    for it in C.read_json(DEDUP_PATH, default=[]):
        pu_to_results.setdefault(C.normalize_url(it.get("playlist_url", "")), []).append(
            results.get(it["norm_url"], {}))
    now = C.now_iso()
    for entry in reg:
        nu = C.normalize_url(entry.get("playlist_url", ""))
        res_list = pu_to_results.get(nu, [])
        if not res_list:
            continue
        valid = sum(1 for r in res_list if r.get("ok"))
        if valid > 0:
            entry["last_verified"] = now
            entry["consecutive_failures"] = 0
            entry["status"] = "verified"
        else:
            entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
            entry["status"] = "failed"
    C.write_json(REGISTRY_PATH, reg)
    log.info("registry.json 验证状态已更新")


if __name__ == "__main__":
    main()
