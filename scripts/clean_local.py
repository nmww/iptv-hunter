#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clean_local.py — 本地网络自检，只保留「你这边真能播」的源（个人自用最准）

为什么需要它：
  GitHub Actions 的验证服务器在境外，很多源在境外能连、回国内（你家宽带）却被
  地理封锁 / 限速 —— 服务器验证通过 ≠ 你这边能播。本脚本在你自己的电脑上跑，
  用你自己的网络逐条探测，只把「你这边 ffprobe 能取到编码」的源写进 iptv-clean.m3u。
  ffprobe 拿不到编码的流，VLC 同样放不了，因此这个判断对你而言是准的。

前置（本机需装）：
  - ffmpeg / ffprobe：Mac 用 `brew install ffmpeg`；Linux 用 `apt install ffmpeg`
  - curl：一般系统自带

用法：
  python3 scripts/clean_local.py
      # 默认读取已发布的订阅地址，输出 output/iptv-clean.m3u
  python3 scripts/clean_local.py --input /path/to/iptv.m3u --output output/iptv-clean.m3u
  python3 scripts/clean_local.py --workers 24 --timeout 8
  python3 scripts/clean_local.py --group 央视,卫视
      # 只保留 group-title 命中的分组（逗号分隔；不填则全保留）
"""

import os
import sys
import json
import time
import argparse
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
DEFAULT_INPUT = "https://github.com/nmww/iptv-hunter/releases/latest/download/iptv.m3u"
DEFAULT_OUTPUT = OUTPUT_DIR / "iptv-clean.m3u"
UA = "Mozilla/5.0 (compatible; iptv-hunter-clean/1.0)"


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


def layer1(url, timeout):
    rc, out, _ = _run(
        ["curl", "-I", "-m", str(timeout), "-A", UA, "-s", "-o", "/dev/null",
         "-w", "%{http_code}"], timeout + 3)
    try:
        return rc == 0 and int(out.strip()) in (200, 302)
    except ValueError:
        return False


def layer2(url, timeout):
    """ffprobe 取编码信息；能取到即视为真实可播流（VLC 也能放）。"""
    rc, out, _ = _run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,width,height", "-of", "json", url], timeout + 10)
    if rc != 0:
        return False, {}
    try:
        data = json.loads(out)
        streams = data.get("streams", [])
        if not streams:
            return False, {}
        s = streams[0]
        return True, {"codec": s.get("codec_name", ""),
                      "w": s.get("width", 0), "h": s.get("height", 0)}
    except Exception:  # noqa: BLE001
        return False, {}


def check(item, timeout):
    url = item["url"]
    t0 = time.time()
    # 第一层只作快速预筛；即便 curl 被 403/地理封锁，仍交给 ffprobe 判定
    l1 = layer1(url, timeout)
    ok2, info = layer2(url, timeout)
    rt = int((time.time() - t0) * 1000)
    return {
        "url": url, "extinf": item["extinf"], "ok": ok2,
        "rt": rt, "l1": l1,
        "codec": info.get("codec", ""), "w": info.get("w", 0), "h": info.get("h", 0),
    }


def parse_m3u(text):
    """返回 [(extinf_line, url), ...]，保留原始 EXTINF 整行（含台标/分组属性）。"""
    out = []
    lines = text.splitlines()
    i = 0
    cur_extinf = None
    for ln in lines:
        ln = ln.rstrip("\n")
        if ln.startswith("#EXTINF"):
            cur_extinf = ln
        elif ln.startswith("#") or not ln.strip():
            continue
        else:
            if cur_extinf is not None:
                out.append((cur_extinf, ln.strip()))
                cur_extinf = None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=DEFAULT_INPUT, help="输入 m3u（本地路径或 URL）")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 m3u")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--timeout", type=int, default=8, help="单条 ffprobe 超时(秒)")
    ap.add_argument("--group", default="", help="只保留 group-title 命中的分组(逗号分隔)")
    args = ap.parse_args()

    # 检查依赖
    for bin_ in ("ffprobe", "curl"):
        if subprocess.run(["which", bin_], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).returncode != 0:
            sys.exit(f"缺少依赖 {bin_}：请先安装 ffmpeg（含 ffprobe）与 curl。")

    # 读取输入
    if args.input.startswith("http://") or args.input.startswith("https://"):
        print(f"下载订阅源：{args.input}")
        req = urllib.request.Request(args.input, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode("utf-8", "replace")
    else:
        text = Path(args.input).read_text(encoding="utf-8", errors="replace")

    pairs = parse_m3u(text)
    total = len(pairs)
    print(f"解析到 {total} 条频道，开始本地网络探测（workers={args.workers}）...")

    items = [{"extinf": e, "url": u} for e, u in pairs]
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(check, it, args.timeout): it for it in items}
        done = 0
        for f in as_completed(futs):
            results.append(f.result())
            done += 1
            if done % 100 == 0 or done == total:
                print(f"  进度 {done}/{total}")

    # 分组过滤（可选）
    want = [g.strip() for g in args.group.split(",") if g.strip()]
    def in_group(extinf):
        if not want:
            return True
        import re
        m = re.search(r'group-title="([^"]*)"', extinf)
        if not m:
            return False
        return any(g in m.group(1) for g in want)

    ok = [r for r in results if r["ok"] and in_group(r["extinf"])]
    # 按响应时间升序（快的在前），同频道多个源自然容灾
    ok.sort(key=lambda x: x["rt"])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output)
    lines = ['#EXTM3U url-tvg="https://epg.pw/api/epg.xml"',
             f"# 本地自检于 {time.strftime('%Y-%m-%d %H:%M')}，仅保留本机网络可播源：{len(ok)}/{total}"]
    for r in ok:
        lines.append(r["extinf"])
        lines.append(r["url"])
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    fail = total - len([r for r in results if r["ok"]])
    print(f"\n完成：保留 {len(ok)} 条可用，剔除 {fail} 条不可播（本机网络判定）。")
    print(f"已写入：{out_path}")
    print("VLC 用法：媒体 → 打开文件 → 选这个 iptv-clean.m3u，或直接拖进 VLC。")


if __name__ == "__main__":
    main()
