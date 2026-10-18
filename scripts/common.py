#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
common.py — iptv-hunter 共享工具模块

提供：日志初始化、带 UA + 超时 + 重试的 HTTP 抓取、配置读取、
JSON 安全读写、字符串归一化等通用能力。所有其他脚本均 import 本模块，
避免重复实现，并统一遵守「外部请求必须带浏览器 UA 与超时」的红线。
"""

import os
import sys
import json
import time
import random
import logging
import configparser
from pathlib import Path

# 项目根目录（scripts/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
SOURCES_DIR = PROJECT_ROOT / "sources"
OUTPUT_DIR = PROJECT_ROOT / "output"
LOG_DIR = OUTPUT_DIR / "log"
CONFIG_PATH = PROJECT_ROOT / "config.ini"

# 统一的浏览器 UA 伪装，避免被源站按空 UA / 爬虫 UA 拒绝
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def get_logger(name: str) -> logging.Logger:
    """返回一个带时间戳格式、输出到 stdout 的 logger。"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


# 全局默认 logger
log = get_logger("iptv-hunter")


def load_config(path: Path | None = None) -> configparser.ConfigParser:
    """读取 config.ini；缺失时使用内置默认值，保证脚本在无配置文件时也能跑。"""
    cfg = configparser.ConfigParser()
    cfg.read_dict(_DEFAULT_CONFIG)
    target = path or CONFIG_PATH
    if target.exists():
        cfg.read(str(target), encoding="utf-8")
    else:
        log.warning("未找到 config.ini，使用内置默认值（建议从 config.ini.example 复制）")
    return cfg


def _default_str(cfg, section, key, fallback):
    try:
        return cfg.get(section, key, fallback=fallback)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return fallback


def _default_int(cfg, section, key, fallback):
    try:
        return cfg.getint(section, key, fallback=fallback)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return fallback


def _default_bool(cfg, section, key, fallback):
    try:
        return cfg.getboolean(section, key, fallback=fallback)
    except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
        return fallback


_DEFAULT_CONFIG = {
    "discover": {
        "github_token_env": "GITHUB_TOKEN",
        "max_new_repos_per_day": "50",
        "max_pages_per_query": "5",
        "request_timeout": "15",
        "request_retries": "2",
        "user_agent": DEFAULT_UA,
    },
    "fetch": {
        "timeout": "10",
        "retries": "2",
    },
    "dedup": {
        "same_channel_keep": "3",
    },
    "validate": {
        "workers": "20",
        "layer1_timeout": "5",
        "verify_seconds": "5",
    },
    "retire": {
        "consecutive_failures_threshold": "7",
        "graveyard_revisit_days": "30",
        "repo_stale_days": "90",
    },
    "output": {
        "epg_url": "https://epg.pw/api/epg.xml",
        "groups": "央视,卫视,国际,体育,纪录,少儿",
        "timezone": "Asia/Shanghai",
    },
}


def http_get(url: str, timeout: int = 15, retries: int = 2,
             ua: str = DEFAULT_UA, headers: dict | None = None,
             as_text: bool = True):
    """
    带 UA + 超时 + 重试的 HTTP GET。返回 (status_code, body_text_or_bytes, ok)。
    任何异常都被捕获并以 ok=False 返回，绝不向上抛出中断主流程。
    """
    import requests

    hdrs = {"User-Agent": ua}
    if headers:
        hdrs.update(headers)
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=hdrs, timeout=timeout, stream=False)
            body = resp.text if as_text else resp.content
            return resp.status_code, body, True
        except Exception as e:  # noqa: BLE001 - 故意兜底，单点失败不中断
            last_err = e
            if attempt < retries:
                time.sleep(1 + random.random())
    log.warning("HTTP GET 失败 %s: %s", url, last_err)
    return 0, "", False


def read_json(path: Path, default=None):
    """安全读取 JSON；文件不存在或解析失败返回 default。"""
    if default is None:
        default = []
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:  # noqa: BLE001
        log.warning("读取 JSON 失败 %s: %s", path, e)
    return default


def write_json(path: Path, data) -> bool:
    """原子化写入 JSON（先写临时文件再替换），避免半截文件。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("写入 JSON 失败 %s: %s", path, e)
        return False


def now_iso() -> str:
    """返回带时区的当前时间 ISO 字符串（使用系统本地时区）。"""
    from datetime import datetime, timezone

    # 尽量使用 Asia/Shanghai，但依赖系统；失败时退回本地时区
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Shanghai")
        return datetime.now(tz).isoformat()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc).astimezone().isoformat()


def today_str() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d")


def normalize_url(url: str) -> str:
    """
    URL 归一化（用于去重）：
    - 统一小写 scheme 与 host
    - 去除默认端口
    - 去除常见的「时间戳类」query 参数（t / _ / _t / expires / timestamp / sign 的纯数字值）
    - query 参数按 key 排序，保证同一资源不同参数顺序被视为重复
    """
    import urllib.parse as up

    if not url:
        return ""
    try:
        parts = up.urlparse(url.strip())
        scheme = parts.scheme.lower()
        netloc = parts.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        # 去除默认端口
        if scheme == "http" and netloc.endswith(":80"):
            netloc = netloc[:-3]
        if scheme == "https" and netloc.endswith(":443"):
            netloc = netloc[:-4]
        path = parts.path
        if path and not path.startswith("/"):
            path = "/" + path
        q = up.parse_qsl(parts.query, keep_blank_values=False)
        drop_keys = {"t", "_", "_t", "expires", "timestamp", "ts", "sign", "token", "nonce", "v"}
        filtered = []
        for k, v in q:
            if k.lower() in drop_keys and (v.isdigit() or v == ""):
                continue
            filtered.append((k.lower(), v))
        filtered.sort()
        query = up.urlencode(filtered)
        # 丢弃 fragment（# 后通常只是播放器定位信息）
        return up.urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:  # noqa: BLE001
        return url.strip().lower()


# 繁→简映射表（仅覆盖常见电视频道用字，避免引入大体积依赖）
_TRAD_TO_SIMPLE = {
    "視": "视", "電": "电", "體": "体", "節": "节", "衛": "卫", "電視臺": "电视台",
    "綜藝": "综艺", "紀錄": "纪录", "體育": "体育", "國際": "国际", "動漫": "动漫",
    "戲曲": "戏曲", "資訊": "资讯", "購物": "购物", "少兒": "少儿", "少兒": "少儿",
    "頻道": "频道", "臺": "台", "綜合": "综合", "新聞": "新闻", "電影": "电影",
    "連續劇": "连续剧", "財經": "财经", "音樂": "音乐", "旅遊": "旅游", "科": "科",
}


def normalize_name(name: str) -> str:
    """
    频道名归一化（用于去重）：
    - 全角→半角
    - 去除空格与常见分隔/特殊符号
    - 繁→简
    - 小写（保留原始名用于展示）
    """
    if not name:
        return ""
    s = name
    # 全角转半角
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    s = "".join(out)
    # 繁→简
    for trad, simp in _TRAD_TO_SIMPLE.items():
        s = s.replace(trad, simp)
    # 去除空格与符号
    for ch in " \t-_·•·.()（）[]【】/\\|~*#@!?，。、：:；;,'\"":
        s = s.replace(ch, "")
    s = s.lower()
    # 别名归一化：去除尾部「频道」等冗余后缀，使 "CCTV-1 综合" = "CCTV1综合频道"
    for suffix in ("频道",):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s


def classify_group(name: str) -> str:
    """根据频道名启发式归类到分组（央视/卫视/国际/体育/纪录/少儿/其他）。"""
    n = normalize_name(name)
    if "cctv" in n or "央视" in n or "中央" in n:
        return "央视"
    if "卫视" in n or "tv" in n and ("satellite" in n or "省" in n):
        return "卫视"
    if any(k in n for k in ["cgtn", "国际", "global", "world", "phoenix", "凤凰"]):
        return "国际"
    if any(k in n for k in ["体育", "sport", "cctv5", "足球", "篮球", "nba"]):
        return "体育"
    if any(k in n for k in ["纪录", "document", "discovery", "探索", "history"]):
        return "纪录"
    if any(k in n for k in ["少儿", "卡通", "动画", "kids", "child", "动漫"]):
        return "少儿"
    return "其他"
