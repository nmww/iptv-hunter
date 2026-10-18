# iptv-hunter · 自主发现 IPTV 直播源整合引擎

一个**不预置任何固定源清单**、完全靠运行时网络发现来采集、验证、整合 IPTV 直播源的引擎，
**并内置一组高信任直链种子**作为播放可用性的骨架。每天定时自动搜索全网新出现的源，验证通过即接入、
失效即淘汰，最终在 **GitHub** 上产出可被 VLC / TiviMate / APTV / DIYP 直接订阅的 `iptv.m3u` 直链
（经 jsDelivr CDN 加速，国内可达）。

> ⚠️ 合规提示：本工具仅聚合**公开发布**的播放列表链接（来自 GitHub 公开仓库 / awesome 元列表 / 高信任种子），
> 自身不托管任何流媒体内容。可用性由代码中的 `ffprobe` / `ffmpeg` 验证链 + 维护者信誉为保障，
> 而非人工“记忆”。请遵守你所在国家/地区的法律法规，勿将产出用于商业分发。

---

## 一、最终订阅地址（播放器直接填这个）

> ⚠️ 国内宽带实测（2026-10-18）：`github.com` 直连下载、`raw.githubusercontent.com`、
> `cdn.jsdelivr.net` 默认域名经常**整体连不上或缓存卡死旧版**。以下按实测可达性排序。

**主用（实时最新，实测国内可达）：gh-proxy 加速 raw**

```
https://gh-proxy.com/https://raw.githubusercontent.com/nmww/iptv-hunter/main/output/iptv.m3u
```

**备用 A：jsDelivr gcore 备用域名（实测国内可达，可能有缓存滞后）**

```
https://gcore.jsdelivr.net/gh/nmww/iptv-hunter@main/output/iptv.m3u
```

**备用 B：ghproxy.net（另一个 raw 加速代理）**

```
https://ghproxy.net/https://raw.githubusercontent.com/nmww/iptv-hunter/main/output/iptv.m3u
```

**备用 C：GitHub Release 直链（官方，国内网络可能连不上 github.com）**

```
https://github.com/nmww/iptv-hunter/releases/latest/download/iptv.m3u
```

- **VLC**：`媒体 → 打开网络串流` 粘贴上面地址；或在 VLC 里 `视图 → 播放列表 → 右键“互联网”→ 添加为播客`。
- **TiviMate / APTV / DIYP**：在“添加播放列表”处选“URL”，粘贴上面地址即可。
- 仓库每日更新后，播放器拉取同一 URL 自动获得最新内容（无需改地址）。
- 纯文本版：把上面地址里的 `iptv.m3u` 换成 `iptv.txt` 即可。
- **不要用** `cdn.jsdelivr.net/gh/...@main/...`（国内节点缓存经常卡在旧版，purge 也刷不动）。

> 注：`output/iptv.m3u` 经 GitHub Actions 回写 `main` 分支，并同步发布到 `latest` 发行版。
> 若看到「本批次无有效源」，通常是运行尚未结束或当天发现层为空，稍后刷新即可。

---

## 二、项目结构

```
iptv-hunter/
├── sources/
│   ├── seeds.json           # 冷启动种子（仅 awesome-iptv 类元列表仓库）
│   ├── curated.json         # 【骨架】高信任直链种子（iptv-org/fanmingming 等，免验证直接收录）
│   ├── registry.json        # 动态源注册表（发现引擎自动维护）
│   ├── graveyard.json       # 淘汰源墓地（30 天后自动回访复检）
│   └── local/               # 本地静态补充 m3u（可选，手动放入，不入库）
├── scripts/
│   ├── common.py            # 共享工具（HTTP/日志/归一化/配置）
│   ├── discover_sources.py  # 【核心】发现层：全网搜索新源（4 渠道）
│   ├── retire_sources.py    # 淘汰与回访（registry ↔ graveyard）
│   ├── fetch_sources.py     # 抓取层：下载 m3u 并解析频道（含 curated + registry + local）
│   ├── deduplicate.py       # 去重引擎（URL + 频道名双重归一化）
│   ├── validate_streams.py  # 流级验证（三层：HTTP/ffprobe/ffmpeg，跳过高信任源）
│   ├── generate_m3u.py      # M3U 生成（分组/EPG/台标/时间戳/每频道保最快 N 条）
│   └── run_all.sh           # 一键跑完整流水线
├── output/
│   ├── iptv.m3u             # 最终输出（自动生成，已入库）
│   ├── iptv.txt             # 文本格式
│   └── log/                 # 验证/更新日志
├── .github/workflows/daily.yml       # GitHub Actions 每日自动运行（免费，单一平台）
├── config.ini               # 全局配置（并发/阈值/淘汰规则）
└── requirements.txt
```

---

## 三、registry.json 字段说明

每条记录代表一个被发现的播放列表源：

| 字段 | 含义 |
|------|------|
| `source_repo` | 来源 GitHub 仓库 `owner/name` |
| `playlist_url` | 播放列表 raw 直链 |
| `discovered_via` | 发现渠道：`github_search` / `recursive` / `awesome` / `search_engine` |
| `discovered_at` | 发现时间（ISO 8601 带时区） |
| `stars` | 仓库 star 数（发现时快照） |
| `last_push` | 仓库最近 push 时间 |
| `last_verified` | 最近一次验证通过时间；`null` 表示从未通过 |
| `consecutive_failures` | 连续验证失败天数，达到阈值即淘汰 |
| `status` | `discovered` / `verified` / `failed` / `retired` |

---

## 四、淘汰与回访规则

- **淘汰**：某源连续 `consecutive_failures` ≥ `retire.consecutive_failures_threshold`（默认 7）天验证失败
  → 移入 `graveyard.json`，记录 `retire_reason`，避免误杀“暂时失效但会复活”的源（换域名/迁移）。
- **回访**：`graveyard.json` 中的源每 `retire.graveyard_revisit_days`（默认 30）天自动复活重准入，
  由下一轮验证决定去留。
- **仓库陈旧**：`last_push` 超过 `retire.repo_stale_days`（默认 90）天的仓库，发现层准入直接拒绝。

---

## 五、如何手动添加种子 / 静态源

1. **加高信任直链种子（骨架来源）**：编辑 `sources/curated.json`，追加一项
   `{"name": "...", "url": "https://.../x.m3u", "trust": "high"}`。
   这类源由维护者持续更新、可用性通常较高，但**同样要过逐条验证**才会被收录
   （不再免验证——死链/被地理封锁的也会被过滤掉）。**优先用这种方式扩充资源。**
2. **加发现入口（元列表）**：编辑 `sources/seeds.json`，追加 `type:"meta"` 的 awesome 类仓库。
   注意：种子只允许 awesome 元列表，**不要**把具体 IPTV 源地址写进 seeds.json。
3. **加固定本地源**：把任意 `.m3u` 文件放进 `sources/local/`，`fetch_sources.py` 会自动合入
   （这些文件被 `.gitignore` 忽略，不会上传到仓库）。

---

## 六、本地运行

```bash
pip install -r requirements.txt          # 需要 ffmpeg/ffprobe 已安装并在 PATH
bash scripts/run_all.sh                  # 跑全量流水线（含推送）
DRY=1 bash scripts/run_all.sh            # 各阶段 dry-run，不写文件/不推送
```

单独调试某个模块：`python scripts/discover_sources.py --dry-run` 等。

### 本地网络自检（个人自用最准，强烈推荐）

GitHub Actions 的验证服务器在**境外**，它验证通过 ≠ 你国内宽带能播（地理封锁/限速）。
要拿到「你这边真能放」的列表，用本机跑自检脚本（用你家网络逐条探测）：

```bash
# 本机先装 ffmpeg（含 ffprobe）：Mac `brew install ffmpeg`；Linux `apt install ffmpeg`
python3 scripts/clean_local.py                 # 读已发布订阅，输出 output/iptv-clean.m3u
python3 scripts/clean_local.py --group 央视,卫视   # 只保留指定分组
python3 scripts/clean_local.py --workers 24 --timeout 8
```

脚本只保留「ffprobe 能取到编码」的源（VLC 同样放不了拿不到编码的流），按响应速度排序，
输出 `output/iptv-clean.m3u`，VLC 直接打开即可。这是剔除「国内连不上」源最准的办法。

---

## 七、部署（每日自动更新，免费，GitHub 单一平台）

> 本项目**仅托管在 GitHub 公开仓库**（国内 Gitee 对 IPTV 聚合类内容有合规风险，已主动撤离）。
> 产物通过 **GitHub Actions** 回写本仓库 `main` 分支，并发布到 `latest` 发行版——
> 订阅主用地址走 GitHub 发行 CDN（`releases/latest/download/iptv.m3u`，国内稳、无浮动缓存延迟）。
> jsDelivr 镜像仅作备用（部分国内节点缓存滞后）。

### GitHub Actions 自动定时（免费，已配好）
1. 工程已位于 `nmww/iptv-hunter`（公开仓库）。
2. 无需任何密钥：`GITHUB_TOKEN` 由 GitHub 自动注入（发现层搜索配额即用）。
3. 工作流在 `.github/workflows/daily.yml`，**每天北京时间 00:00（= UTC 16:00）** 自动跑。
4. 每次运行结束会：① 发布/更新 `latest` 发行版（更新主用订阅地址）；② `purge` jsDelivr 备用缓存。
5. Actions 标签页 → 点 **“Run workflow”** 可手动立即触发一次。

### 本地 / 云服务器定时任务（等价效果，无需 GitHub Actions）
```bash
# 每天北京 02:00 执行（crontab -e）；产物自行 git 提交回仓库即可
0 2 * * * cd /path/to/iptv-hunter && GITHUB_TOKEN=yyy bash scripts/run_all.sh >> output/log/cron.log 2>&1 && \
  cd /path/to/iptv-hunter && git add output/ && git commit -m "update" && git push origin main

---

## 八、常见故障排查表

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| `iptv.m3u` 只有头部注释「本批次无有效源」 | 运行尚未结束 / 当天发现层 0 入库 / 验证全挂 | 手动触发一次流水线，看 Actions 日志的「新增源条目」数 |
| 订阅地址拉到的是旧内容 | jsDelivr `@main` 浮动缓存滞后（国内节点尤甚） | 主用改走 Release 直链 `releases/latest/download/iptv.m3u`；或临时用 raw 备用直链 |
| 发现层新增 = 0 | 查询词被二次编码 / 准入 stars 门槛过狠 | 已修复：查询改原始字符 + 准入不再卡 stars；必要时 `discover --dry-run` 观察 |
| `ffprobe/ffmpeg` 报错 | 本机未装或不在 PATH | `brew install ffmpeg`（Mac）/ `apt install ffmpeg`（Linux） |
| 验证极慢 / CI 超时 | 流条数过多 | `config.ini` 设 `validate.max_streams` 小值（如 200） |
| 大量源很快被淘汰 | 源本身失效 / 网络出口被地理封锁 | 属正常；引擎会自动替换，可放宽 `consecutive_failures_threshold` |

---

## 九、三条自查命令（阶段 4）

```bash
# ① 发现层每天是否真的搜到新仓库（观察输出，不触动验证/推送）
python scripts/discover_sources.py --dry-run

# ② registry.json 规模与状态分布是否随时间稳步增长
python3 -c "import json;r=json.load(open('sources/registry.json'));from collections import Counter;print(Counter(x['status'] for x in r));print('总数',len(r))"

# ③ graveyard.json 中的源是否在 30 天后被回访复检复活
python3 -c "import json;g=json.load(open('sources/graveyard.json'));print('墓地',len(g));[print(x['source_repo'],x.get('last_revisit')) for x in g[:10]]"
```

---

## 十、设计红线（已落实到代码）

1. **发现层禁止白名单**：除 `seeds.json` 的 awesome 元列表外，发现引擎不硬编码任何具体源地址。
2. **高信任种子是显式例外**：`sources/curated.json` 收录的维护良好公开 m3u（iptv-org / fanmingming 等）
   作为播放可用性的骨架，免逐条验证直接收录——这是**有意**的可靠来源，而非“预置清单”作弊。
3. **代码即猎手**：发现由 Python（GitHub Search API + HTML 解析）完成，而非人工列举。
4. **质量硬门槛**：registry 中的发现源必须过仓库级准入 + 三层流级验证才入库。
5. **动态注册表**：registry / graveyard 的增删由程序自动完成，禁止人工维护。
