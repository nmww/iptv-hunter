# iptv-hunter · 自主发现 IPTV 直播源整合引擎

一个**不预置任何固定源清单**、完全靠运行时网络发现来采集、验证、整合 IPTV 直播源的引擎。
每天定时自动搜索全网新出现的源，验证通过即接入、失效即淘汰，最终在 Gitee 上产出可被
VLC / TiviMate / APTV / DIYP 直接订阅的 `iptv.m3u` 直链。

> ⚠️ 合规提示：本工具仅聚合**公开发布**的播放列表链接（来自 GitHub 公开仓库 / awesome 元列表），
> 自身不托管任何流媒体内容。可用性由代码中的 `ffprobe` / `ffmpeg` 验证链保障，
> 而非人工“记忆”。请遵守你所在国家/地区的法律法规，勿将产出用于商业分发。

---

## 一、最终订阅地址（播放器直接填这个）

```
https://gitee.com/markets2022/iptv-hunter/raw/master/output/iptv.m3u
```

- **VLC**：`媒体 → 打开网络串流` 粘贴上面地址；或在 VLC 里 `视图 → 播放列表 → 右键“互联网”→ 添加为播客`。
- **TiviMate / APTV / DIYP**：在“添加播放列表”处选“URL”，粘贴上面地址即可。
- 仓库每日更新后，播放器拉取同一 URL 自动获得最新内容（无需改地址）。
- 纯文本版：`https://gitee.com/markets2022/iptv-hunter/raw/master/output/iptv.txt`

> 注：首次运行流水线（或手动执行）后 `iptv.m3u` 才会有内容；在此之前文件仅含头部注释。

---

## 二、项目结构

```
iptv-hunter/
├── sources/
│   ├── seeds.json           # 冷启动种子（仅 awesome-iptv 类元列表仓库）
│   ├── registry.json        # 动态源注册表（程序自动维护）
│   ├── graveyard.json       # 淘汰源墓地（30 天后自动回访复检）
│   └── local/               # 本地静态补充 m3u（可选，手动放入，不入库）
├── scripts/
│   ├── common.py            # 共享工具（HTTP/日志/归一化/配置）
│   ├── discover_sources.py  # 【核心】发现层：全网搜索新源（4 渠道）
│   ├── retire_sources.py    # 淘汰与回访（registry ↔ graveyard）
│   ├── fetch_sources.py     # 抓取层：下载 m3u 并解析频道
│   ├── deduplicate.py       # 去重引擎（URL + 频道名双重归一化）
│   ├── validate_streams.py  # 流级验证（三层：HTTP/ffprobe/ffmpeg）
│   ├── generate_m3u.py      # M3U 生成（分组/EPG/时间戳/每频道保最快 N 条）
│   ├── push_to_gitee.sh     # 推送脚本（本地：git push 方式）
│   ├── push_to_gitee_api.py # 推送脚本（CI：Gitee API 更新文件，幂等无冲突）
│   └── run_all.sh           # 一键跑完整流水线
├── output/
│   ├── iptv.m3u             # 最终输出（自动生成，已入库）
│   ├── iptv.txt             # 文本格式
│   └── log/                 # 验证/更新日志
├── .github/workflows/daily.yml       # GitHub Actions 每日自动运行（免费）
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

1. **加发现入口（元列表）**：编辑 `sources/seeds.json`，追加 `type:"meta"` 的 awesome 类仓库。
   注意：种子只允许 awesome 元列表，**不要**把具体 IPTV 源地址写进 seeds.json。
2. **加固定本地源**：把任意 `.m3u` 文件放进 `sources/local/`，`fetch_sources.py` 会自动合入
   （这些文件被 `.gitignore` 忽略，不会上传到仓库）。

---

## 六、本地运行

```bash
pip install -r requirements.txt          # 需要 ffmpeg/ffprobe 已安装并在 PATH
bash scripts/run_all.sh                  # 跑全量流水线（含推送）
DRY=1 bash scripts/run_all.sh            # 各阶段 dry-run，不写文件/不推送
bash scripts/run_all.sh --max 100        # 验证阶段最多 100 条（CI 省时）
```

单独调试某个模块：`python scripts/discover_sources.py --dry-run` 等。

---

## 七、部署（每日自动更新，免费）

> ⚠️ Gitee Go 是付费服务，本项目已改用 **GitHub Actions（公开仓库永久免费、原生 cron）** 做每日自动运行，
> 计算结果通过 Gitee API 推送回 Gitee，VLC 订阅直链不变（还是 Gitee raw 那个地址）。

### 方式 A：GitHub Actions 自动定时（免费，推荐）
1. 把工程推到你的 **GitHub 公开仓库**（如 `nmww/iptv-hunter`）。
2. 仓库 → **Settings → Secrets and variables → Actions → New repository secret** 添加：
   - `GITEE_PAT`：你的 Gitee 私人令牌（值就是建仓库时用的那个，需 `projects` 权限）
   - （`GITHUB_TOKEN` 由 GitHub **自动注入**，无需配置，发现层直接享受 5000/h 搜索配额）
3. 工作流已放在 `.github/workflows/daily.yml`，**每天北京时间 00:00（= UTC 16:00）** 自动跑。
4. Actions 标签 → 点 **“Run workflow”** 可手动立即触发一次，验证全流程。

> 产物通过 `scripts/push_to_gitee_api.py`（Gitee contents API）更新到 Gitee，
> 与代码仓库 git 历史解耦，永远不会出现“非快进推送冲突”。

### 方式 B：本地 / 云服务器定时任务（等价效果，无需 GitHub）
```bash
# 每天北京 02:00 执行（crontab -e）
0 2 * * * cd /path/to/iptv-hunter && GITEE_PUSH_TOKEN=xxx GITEE_REPO_URL=https://oauth2:xxx@gitee.com/markets2022/iptv-hunter.git GITHUB_TOKEN=yyy bash scripts/run_all.sh >> output/log/cron.log 2>&1
```

---

## 八、常见故障排查表

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| `iptv.m3u` 只有头部注释 | 首次尚未跑通验证 | 手动触发一次流水线 / `run_all.sh` |
| 发现层几乎搜不到仓库 | 未配置 `GITHUB_TOKEN`，匿名 60/h 限流 | 在流水线密钥配 `GITHUB_TOKEN` |
| `ffprobe/ffmpeg` 报错 | 本机未装或不在 PATH | `brew install ffmpeg`（Mac）/ `apt install ffmpeg`（Linux） |
| 验证极慢 / CI 超时 | 流条数过多 | `config.ini` 设 `validate.max_streams` 小值（如 200） |
| 推送失败 `exit 1` | 令牌无权限 / 分支名不对 | 确认令牌含 `projects`；分支 `master`/`main` 与仓库一致 |
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

1. **禁止白名单**：除 `seeds.json` 的 awesome 元列表外，无任何硬编码源地址；全部运行时发现。
2. **代码即猎手**：发现由 Python（GitHub Search API + HTML 解析）完成，而非人工列举。
3. **质量硬门槛**：每个源必须过仓库级准入 + 三层流级验证才入库。
4. **动态注册表**：所有源的增删由程序自动完成，禁止人工维护 `registry.json`。
