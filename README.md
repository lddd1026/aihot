# aihot · AI 每日热点雷达

每天 17:00 前，把最近 24 小时的 AI 热点精选 Top 10~15，以「一张长图 + 可点击文字」推送到企业微信和邮箱。

> 完整需求、架构与开发计划见 [PLAN.md](PLAN.md)；设计背景、亮点难点与容灾设计见 [DESIGN.md](DESIGN.md)。

## 环境要求

- Windows 10/11
- Python 3.12（本项目使用独立 venv，不污染系统/Anaconda 环境）
- Microsoft Edge（系统自带，用于日报长图渲染）

## 安装

```powershell
cd D:\信息收集整理\aihot

# 1. 创建独立虚拟环境（使用 Python 3.12）
py -3.12 -m venv .venv

# 2. 安装依赖（含开发依赖；国内建议使用清华镜像）
.venv\Scripts\python.exe -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 配置

复制 `config.example.yaml` 为 `config.yaml`，填入：

| 配置项 | 说明 |
|---|---|
| `push.wecom.webhook` | 企业微信机器人 Webhook 地址 |
| `llm.api_key` | DeepSeek API key（或设置环境变量 `AIHOT_LLM_KEY`） |
| `push.email.*` | 邮箱与 SMTP 授权码（或环境变量 `AIHOT_EMAIL_PASSWORD`） |
| `proxy.*` | 需要访问 Reddit / Hugging Face 时启用 |

> `config.yaml` 含密钥，已被 `.gitignore` 排除，切勿外传。

## 定时任务（无人值守）

```powershell
# 安装（默认每天 17:00；可加 -Time 07:30 自定义）
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1

# 手动跑一次（验证用）
Start-ScheduledTask -TaskName aihot-daily
Get-ScheduledTaskInfo -TaskName aihot-daily

# 卸载
powershell -ExecutionPolicy Bypass -File scripts\unregister_task.ps1
```

| 任务设置 | 值 | 原因 |
|---|---|---|
| 触发时间 | 每天 17:00 | 留 30 分钟给抓取/加工/推送，17:30 前送达 |
| 仅用户登录时运行 | 是 | **代理软件依附用户会话**，服务模式下不可用 |
| 唤醒计算机运行 | 是 | 睡眠状态也能按时运行 |
| 错过后尽快启动 | 是 | 开机/唤醒后自动补跑 |
| 执行限时 | 30 分钟 | 防止异常卡死 |
| 重复启动策略 | IgnoreNew | 避免同一时段重复推送 |

每日入口 `run_daily.bat` 会先做**快速预检**，再决定是否运行：

| 预检项 | 失败后果 | 说明 |
|---|---|---|
| LLM（DeepSeek，**直连**） | ❌ 致命：记录跳过 + 告警 | key 失效/欠费属此类 |
| 企业微信 webhook（**直连**） | ❌ 致命：记录跳过 + 告警 | key 失效属此类 |
| 代理（Clash） | ⚠️ **可降级**：自动改直连继续跑 | 只影响需要代理的墙外源，国内源照常 |

跳过的运行会写入 `runs` 表（`status=skipped` + 原因），`aihot report` 可区分"跳过"与"没运行"。
告警通道为**企微 + 邮件双通道并送**（谁先成功即算送达；即使你不常登录企业微信，邮件也能兜住），
两者都失败才写本地 `logs\ALERTS.log`；所有通道**一律直连**，不会因代理挂掉而静默失败。

## 降级与容灾策略

| 场景 | 行为 |
|---|---|
| 代理不可用（Clash 没开 / 端口开着但节点坏） | **健康检查**判定后自动降级为直连（短超时），国内源照常出报，并推送一条「运行降级提示」 |
| 单个信息源失败 | 单源隔离，不影响其他源（`runs` 表记录 `sources_failed`） |
| 长图渲染失败 | 降级为纯文字推送（正文会带 ⚠️ 提示） |
| 企微推送失败 | 自动转**邮件**补发；两者都失败才记为未送达并告警 |
| 告警发不出去 | 写入 `logs\ALERTS.log` 本地兜底 |
| 当天没有新内容 | 不生成日报（避免重复推送旧内容） |
| 任务错过触发时间 | 开机/唤醒后自动补跑（StartWhenAvailable） |

## 排障手册

| 现象 | 排查步骤 |
|---|---|
| **没收到日报** | ① `.venv\Scripts\python.exe -m aihot report --days 7` 看是"跳过（有原因）"还是"没运行" ② `logs\run_daily.log` 看 `PREFLIGHT FAILED` ③ `logs\ALERTS.log` 看告警兜底记录 ④ `aihot check --net` 看具体哪项失败 |
| 预检失败：LLM | DeepSeek key 失效/欠费 → `aihot check --net` 会显示鉴权失败 |
| 预检失败：企业微信 | webhook 被移除或接口异常；群里机器人是否还在 |
| 部分信息源失败 | **属正常**（单源隔离）；`aihot check --net` 可看各源健康度；常见原因是被墙站点需要代理 |
| 需要代理的源失败 | 确认代理软件已启动**且节点可用**（Clash 里点「测试延迟」，全红＝节点挂了） |
| 想手动补发 | `aihot resend --date 2026-09-24`（默认今天）；`--channel wecom` 只发企业微信 |
| 想看看今天长什么样 | `aihot run --dry-run`（不推送，产出文件） |
| 想看某天存档 | `aihot preview --date 2026-09-24` |
| 成本异常 | `runs` 表的 `cost_cny` 字段；想零成本调试用 `aihot enrich --no-llm` |
| 代理没开 / 节点坏 | **无需手动处理**：健康检查判定后自动降级直连（国内源照常），会收到一条降级提示；`aihot check --quick` 会显示"端口可连接但节点异常" |
| 完全重来一天 | 删掉该日 `data\digests\*.jpg/md/html`，再 `aihot run`（已推送过则用 `resend`） |

## 运维速查

```powershell
.venv\Scripts\python.exe -m aihot check --quick    # 关键依赖预检（3~5 秒）
.venv\Scripts\python.exe -m aihot check --net      # 全量探测（含各信息源健康度）
.venv\Scripts\python.exe -m aihot alert --message "test"      # 验证告警通道（企微 + 邮件，失败写本地文件）
.venv\Scripts\python.exe -m aihot report --days 14            # 稳定性报告（SLO 对照）
.venv\Scripts\python.exe -m aihot report --days 14 --json     # 同上，JSON 输出
```

## 信息源

| 类别 | 源 | 说明 |
|---|---|---|
| 中文媒体 | 量子位、InfoQ 中文 | RSS |
| 英文媒体 | TechCrunch AI、The Verge AI、VentureBeat AI、Meta 工程博客 | RSS |
| 官方发布 | OpenAI News、Google DeepMind、Anthropic News | RSS / 网页解析 |
| 社区 | Hacker News、Reddit（LocalLLaMA + MachineLearning） | API / RSS |
| 论文 | arXiv（cs.AI/cs.CL/cs.LG，日报「论文精选」每天 ≤2 篇） | 官方 API |
| 开源项目 | **GitHub 近 7 天增长最快项目**（近 7 天新建 + star 最多；精选区最多 4 条） | 官方 Search API |
| 开源项目 | **GitHub 当日热榜**（当天 star 增速最快；网页解析，偶发 403/超时属预期） | Trending 页 |
| 待启用 | Hugging Face Daily Papers（本机 IP 持续 429） | — |

> 机器之心站点已改版为「数据服务」落地页，暂无可抓列表；VentureBeat/DeepMind/Anthropic/Reddit 需要代理。

## 常用命令

```powershell
# 配置与连通性自检（离线）
.venv\Scripts\python.exe -m aihot check

# 附加网络探测（代理 / 企业微信 / LLM / 各信息源可达性）
.venv\Scripts\python.exe -m aihot check --net

# 记录一次"跳过运行"并告警（预检失败时由 run_daily.bat 调用，一般无需手动执行）
.venv\Scripts\python.exe -m aihot skip --reason "manual maintenance"

# 采集一次并落库（不推送）
.venv\Scripts\python.exe -m aihot fetch

# 完整流程（采集 → 加工 → 日报 → 推送）
.venv\Scripts\python.exe -m aihot run

# dry-run：不推送，只产出 data\digests\ 三件套
.venv\Scripts\python.exe -m aihot run --dry-run

# AI 加工：预筛 → 聚类 → LLM 加工 → 排序（写回结果）
.venv\Scripts\python.exe -m aihot enrich

# 免费模式（只做预筛/聚类/排序，不调 LLM）+ 不写库
.venv\Scripts\python.exe -m aihot enrich --no-llm --dry-run --top 15

# 只采集指定源 / 预览不落库 / 自定义时间窗口
.venv\Scripts\python.exe -m aihot fetch --source qbitai --source openai_news --dry-run --window-hours 48

# 代码质量
.venv\Scripts\python.exe -m ruff check .        # 静态检查
.venv\Scripts\python.exe -m pytest              # 单元测试
```

### 退出码约定

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 运行期错误（网络探测失败、全部源采集失败等） |
| 2 | 命令行用法错误（argparse） |
| 3 | 配置错误（配置校验未通过 / 日志目录不可用） |

## 目录说明

```
aihot/
├── PLAN.md               # 需求 / 架构 / 开发计划 / 质量门禁
├── config.yaml           # 本地配置（含密钥，不入库）
├── src/aihot/            # 源码
│   ├── cli.py            # 命令行入口
│   ├── config.py         # 配置加载与校验
│   └── ...
├── tests/unit/           # 开发单元测试
├── tests/independent/    # 独立测试 Agent 的验证脚本
├── reports/              # 每阶段门禁测试报告
├── data/                 # SQLite 与日报存档
└── logs/                 # 运行日志（JSON lines）
```

## 开发进度

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 工程基建（骨架/配置/日志/CLI） | ✅ 通过独立门禁（`reports/M0-test-report.md`） |
| M1 | 采集层（HTTP/RSS/HN/存储/fetch） | ✅ 通过独立门禁（`reports/M1-test-report.md`） |
| M2 | 加工层（预筛/聚类/LLM/排序） | ✅ 通过独立门禁（`reports/M2-test-report.md`） |
| M3 | 呈现 + 推送（端到端） | ✅ 通过独立门禁（`reports/M3-test-report.md`） |
| M4 | 自动化 + 运维加固 | ✅ 通过独立门禁（`reports/M4-test-report.md`） |
| M5 | 稳定性观察期 | ⚠️ 第 1 轮未通过（代理静默失败）→ 已修复（`docs/M5-观察期报告.md`，含独立复验 `reports/M5-fix-test-report.md`），**第 2 轮观察中**（2026-10-12 起） |

> **文档同步规则**：任何改动必须同步更新 README / DESIGN / PLAN / docs（清单见 `PLAN.md` 附录 B）。
> 项目复盘（个人贡献/核心问题/方案设计/技术取舍/验证方式/实际结果）见 `docs/PROJECT-REVIEW.md`。
