# aihot · 项目计划书

> 版本 v1.0 ｜ 冻结日期 2026-09-23 ｜ 状态：**已冻结**
> 本文档是项目的唯一需求与计划依据（需求规格 / 架构设计 / 开发计划 / 质量门禁）。
> 项目目录：`D:\信息收集整理\aihot`

---

## 0. 项目概述

**一句话定位**：每天 17:30 前，把最近 24 小时的 AI 热点精选 Top 10~15，以「一张长图 + 可点击文字」推送到企业微信和邮箱。

| 项 | 内容 |
|---|---|
| 运行方式 | Windows 本机，任务计划程序每天 17:00 自动执行 |
| 技术栈 | Python 3.12 + SQLite + DeepSeek API + Playwright（驱动系统 Edge 截图） |
| 推送渠道 | 企业微信机器人（主）+ 邮件（完整版同步） |
| 兴趣偏好 | 模型与技术进展、产品与应用、开源与工具（排序加权，不硬过滤） |
| 网络环境 | 本机有代理，可访问外网 |

### 非目标（明确不做）

- ❌ 用户系统 / 多用户 / Web 服务 / 移动 App
- ❌ 实时推送（每天一次；重要消息加推不在 v1 范围）
- ❌ 微博、知乎、36氪、X/Twitter
- ❌ 容器化 / 云部署（本地 Windows 运行，上服务器另行立项）
- ❌ 重 NLP 模型依赖（相似度用 rapidfuzz；不够再评估 embedding）

---

## 1. 需求规格

### 1.1 处理流水线

```
每天 17:00  Windows 任务计划触发（先跑 check --quick 预检，失败立即告警）
   │
   ▼
① 采集      所有源并行抓取，只要最近 24 小时的内容        → 约 200~400 条
   ▼
② 去重聚类  同一事件被多家报道 → 合并成 1 条（"5 家媒体报道"）
   ▼
③ 预筛      AI 相关性过滤 + 剔除活动通知/招聘/课程广告     → 约 60~100 条
   ▼
④ AI 加工   DeepSeek：分类标签 / 中文摘要 / 英文翻译 / 重要性打分
   ▼
⑤ 热度排序  LLM 分 × 源权重 + 社区热度 + 跨源提及次数
   ▼
⑥ 生成日报  今日综述 + Top 12 精选 + 论文精选 + 其他简讯
   ▼
⑦ 推送      企业微信（先图后文）+ 邮件（完整版）
   ▼
⑧ 存档      SQLite + data/digests/ 图片与文字存档
```

### 1.2 信息源清单

| 类别 | 源 | 方式 | 状态 |
|---|---|---|---|
| 中文媒体 | 量子位 | RSS | ✅ 已验证 |
| 中文媒体 | InfoQ 中文 | RSS | ✅ 已验证 |
| 中文媒体 | 机器之心 | 网页解析 | M4 适配 |
| 英文媒体 | TechCrunch AI | RSS | ✅ 已验证 |
| 英文媒体 | The Verge AI / VentureBeat AI | RSS | M4 |
| 社区 | Hacker News | API（points/comments 作热度信号） | ✅ 已验证 |
| 社区 | Reddit r/LocalLLaMA、r/MachineLearning | JSON（走代理） | M4 |
| 官方 | OpenAI News | RSS | ✅ 已验证 |
| 官方 | Anthropic / Google DeepMind / Meta AI | RSS 或解析 | M4 |
| 论文 | Hugging Face Daily Papers（社区投票=最热） | API（走代理） | M4 验证 |
| 论文 | arXiv | 仅作交叉验证，每天最多推 1~2 篇 | M4 |

### 1.3 AI 加工（DeepSeek，两阶段）

- **逐条加工**（每批 10~20 条，JSON 输出）：中文标题 + 一句话摘要（英文自动翻译）、分类标签、重要性 0~100
- **全局综述**：Top 30 输入，输出 150~250 字「今日 AI 大事记」
- 已处理 URL 走缓存，不重复计费；每日成本上限告警
- 兴趣加成：模型与技术 +8、产品与应用 +5、开源与工具 +5（配置可调）

### 1.4 日报形态

- **图片**：浅色极简长图，宽 1080px，HTML 模板 → Playwright 驱动系统 Edge 全页截图 → JPEG；≤2MB 直发，超限自动降质/拆图；存档 `data/digests/`
- **文字**：企微 markdown 精简版（带可点击链接）；邮件 HTML 完整版（内嵌图 + 全文链接）
- **企微发送顺序**：先图片消息，再文字链接条

### 1.5 存储（SQLite 四张表）

| 表 | 用途 |
|---|---|
| `items` | 所有抓取条目 + AI 加工结果（含 url_hash 缓存） |
| `clusters` | 事件簇（多源合并） |
| `digests` | 每日日报存档 |
| `runs` | 运行记录（耗时/条数/token/错误） |

### 1.6 运维要求

- 启动前置预检 `check --quick`（LLM 可达、webhook 可达、代理可用）
- 单源失败只降级不阻断；整体失败发企微告警
- 日志 JSON lines 轮转；密钥全程脱敏
- 任务计划：仅用户登录时运行（代理依赖用户会话）、唤醒计算机、错过后尽快补跑

---

## 2. 生产级验收标准（SLO）

| 维度 | 指标 | 目标值 | 验证方式 |
|---|---|---|---|
| 可用性 | 30 天滚动送达成功率 | ≥ 99%（最多 1 次失败且必有告警+补发） | `runs` 表统计 |
| 准点率 | 约定时间（`app.deliver_by`，当前 17:30）前送达比例 | ≥ 95% | `runs` 表 |
| 幂等性 | 重跑不重复推送、不重复花 LLM 钱 | 100% | 故障演练 |
| 恢复力 | 推送失败后可补发且内容不丢 | 100% | `resend` 演练 |
| 成本 | 日均 LLM 成本 | ≤ ¥1 | token 计费统计 |
| 性能 | 端到端耗时 P95 | < 25 分钟 | `runs` 表 |
| 可观测 | 失败必有企微告警、日志可定位环节 | 100% | 故障演练 |
| 数据 | 日报存档保留 | ≥ 90 天 | 文件检查 |
| 代码 | ruff + pytest 全绿才允许进入下一里程碑 | 硬门槛 | 每里程碑 |

> **项目整体 DoD**：连续 14 天观察期内以上指标全部达标，才判定"完成"。

---

## 3. 架构设计

### 3.1 分层（依赖只能自上而下）

```
        O 编排层   CLI / runner / 任务计划入口
             │
   ┌────┬────┴────┬──────────┬─────────┐
   ▼    ▼         ▼          ▼         ▼
  S采集  P加工    R呈现      D推送      —
   └────┴─────────┴──────────┴─────────┘
             │
        C 基础层  配置 / 数据模型 / SQLite / HTTP / 工具 / 日志
```

**铁律**：下层不感知上层；每层可单独测试、单独替换。

### 3.2 模块清单（20 个模块）

#### C 基础层
| 模块 | 职责 | 关键接口 |
|---|---|---|
| `config` | 加载/校验 `config.yaml`，提供全局配置 | `load_config() -> Config` |
| `models` | 数据契约：RawItem / NewsItem / Cluster / Digest / RunRecord | — |
| `store` | SQLite 读写、查重缓存、存档 | `Store` |
| `net` | 统一 HTTP 客户端（代理/超时/重试/并发） | `HttpClient` |
| `utils` | URL 规范化、文本清洗、时间处理、脱敏 | 函数集合 |
| `logging_setup` | JSON lines 日志 + 密钥脱敏过滤器 | `setup_logging()` |
| `checks` | 诊断探测（代理/LLM/webhook） | `run_net_checks()` |

#### S 采集层
| 模块 | 职责 |
|---|---|
| `sources.base` / `registry` | 适配器接口 + 注册 + 并发调度 + 单源失败隔离 |
| `sources.rss` | 通用 RSS 适配器（覆盖大部分源，新增源≈加配置） |
| `sources.hackernews` | HN API，提取 points/comments |
| `sources.reddit` | Reddit JSON（走代理） |
| `sources.papers` | HF Daily Papers + arXiv 交叉验证 |
| `sources.jiqizhixin` | 机器之心网页解析 |

> 约束：适配器只产出 `RawItem`，不写库、不推送、不调 LLM。

#### P 加工层
| 模块 | 职责 |
|---|---|
| `prefilter` | AI 相关性门槛 + 垃圾过滤 + 历史去重 |
| `dedup` | URL 规范化去重 → 标题相似度聚类（并查集） |
| `enrich` | **唯一调用 LLM 的模块**：批量请求、JSON 容错、缓存、成本统计 |
| `prompts` | 提示词模板（独立于代码，便于调优） |
| `rank` | 综合热度分：55% LLM + 20% 跨源 + 15% 社区 + 10% 源权重 + 兴趣加成 |

#### R 呈现层
| 模块 | 职责 |
|---|---|
| `compose` | 组装日报：选 Top N、分节、统计页脚 |
| `render_text` | 企微 markdown + 邮件 HTML + 本地 md 存档 |
| `render_image` | Jinja2 → HTML → Edge 截图 → JPEG（含体积控制/拆图） |
| `templates/` | `digest.html.j2`（浅色长图）/ `email.html.j2` / `wecom.md.j2` |

> 约束：呈现层只产出文件与消息载荷，不负责发送 → `--dry-run` 可完整预览。

#### D 推送层
| 模块 | 职责 |
|---|---|
| `delivery.base` | Notifier 接口（重试/限流约定） |
| `delivery.wecom` | 图片消息（base64+md5）+ markdown 分片（4096B） |
| `delivery.email` | SMTP 发送 HTML（内嵌图） |

#### O 编排层
| 模块 | 职责 |
|---|---|
| `runner` | 串流程 + 统计 + 异常兜底告警 |
| `cli` | 命令：`check` / `run` / `fetch` / `preview` / `resend` |
| `run_daily.bat` | 任务计划入口 |

### 3.3 数据契约

```
S 采集层 ──RawItem──► P 加工层 ──Digest──► R 呈现层 ──文件+载荷──► D 推送层
             ▲             │
             │             └──NewsItem/Cluster──► C 基础层(store 落库)
             └── 已处理缓存（避免重复花 LLM 的钱）

RawItem  : source_id, title, url, published_at, raw_summary, lang, extra{hn_points,...}
NewsItem : RawItem + cluster_id, category, importance(0-100), cn_title, cn_summary
Cluster  : canonical_title, item_ids[], source_ids[], mention_count, heat_score
Digest   : date, summary, top[], papers[], briefs[], stats{}, files{}
```

### 3.4 目录结构

```
D:\信息收集整理\aihot\
├── PLAN.md                  # 本文档
├── config.yaml              # 唯一配置（含密钥，不进 git）
├── config.example.yaml      # 脱敏模板
├── requirements.txt         # 运行时依赖
├── requirements-dev.txt     # 开发依赖（pytest/ruff）
├── run_daily.bat            # 任务计划入口（M4）
├── data\
│   ├── aihot.db
│   └── digests\             # 每期日报存档（jpg + md）
├── logs\
├── reports\                 # 测试 Agent 专属（门禁报告 + 证据）
├── src\aihot\
│   ├── cli.py  runner.py
│   ├── config.py  models.py  store.py  net.py  utils.py
│   ├── logging_setup.py  checks.py
│   ├── sources\  base.py registry.py rss.py hackernews.py
│   │              reddit.py papers.py jiqizhixin.py
│   ├── pipeline\ prefilter.py dedup.py enrich.py prompts.py rank.py
│   ├── presentation\ compose.py render_text.py render_image.py
│   │                 templates\
│   └── delivery\ base.py wecom.py email.py
└── tests\
    ├── unit\                # 开发自测
    └── independent\         # 测试 Agent 的独立验证脚本
```

### 3.5 关键设计决策

1. **采集与加工解耦**：源只出原始条目，AI 加工是可跳过环节 → 将来可加纯 RSS 聚合模式
2. **呈现与推送解耦**：先渲染成文件再发送 → 出问题可 `resend` 补发，不重跑抓取与 LLM
3. **配置驱动**：源开关、关键词、Top 数、权重、渠道全在 `config.yaml` → 日常调整不改代码

---

## 4. 开发计划

### M0 · 工程基建
- **目标**：项目骨架可运行、质量工具就位、配置可校验
- **交付物**：目录结构 + 独立 venv + 依赖 + ruff/pytest 配置 + 日志框架（JSON lines + 脱敏）+ `config` 加载与校验 + CLI 骨架（`--help` / `check`）
- **边界**：❌ 不写业务逻辑；❌ 不接任何源
- **验收**：`aihot check` 校验配置并给出明确错误；`pytest` 可跑；ruff 无告警；密钥不进日志（脱敏验证）
- **工期**：0.5 天

### M1 · 采集层
- **目标**：稳定抓取第一批源（量子位/InfoQ/TechCrunch/OpenAI/HN），落库可查
- **交付物**：`net`、`sources.rss` + `sources.hackernews`、`store`、源连通性自检、`fetch` 命令
- **边界**：❌ 不做聚类去重；❌ 不调 LLM；❌ 不推送；❌ 不接第二批源
- **验收**：① 断 1 源不影响整体 ② 连跑 3 次幂等 ③ 24h 时间边界正确 ④ 抓取 < 2 分钟 ⑤ 单测通过
- **工期**：1.5 天

### M2 · 加工层
- **目标**：原始条目 → 去重聚类 → 预筛 → LLM 加工 → 排序候选
- **交付物**：`prefilter` / `dedup` / `enrich` / `prompts` / `rank` + 单元测试集
- **边界**：❌ 不做渲染；❌ 不推送；❌ 不引入第二家 LLM
- **验收**：① 聚类准确率 ≥ 90% ② 翻译摘要人工抽检 20 条通过 ③ 坏数据注入不崩溃 ④ 重跑零 LLM 调用 ⑤ 单测全绿 ⑥ 加工 < 12 分钟、成本 < ¥1
- **工期**：2 天

### M3 · 呈现 + 推送（端到端）
- **目标**：手机收到第一份真实日报（图 + 文字 + 邮件）
- **交付物**：`compose` / `render_text` / `render_image` / `delivery.*` / `runner` / `--dry-run` / `resend`
- **边界**：❌ 不接定时任务；❌ 不加新源
- **验收**：① dry-run 三件套排版通过 ② 真机推送成功 ③ 图 1080px/≤2MB/无乱码 ④ 超长自动拆图 ⑤ 推送失败 `resend` 补发不重跑 LLM ⑥ 同日重复 run 不重复推送
- **工期**：2 天

### M4 · 自动化 + 运维加固
- **目标**：无人值守全自动运行，故障可告警可排查
- **交付物**：任务计划 + 告警 + 日志轮转 + 第二批源 + README 排障手册 + 每日预检
- **边界**：❌ 不做 Web/看板；❌ 不做多用户
- **验收（故障演练）**：① 断网演练 ② LLM key 失效演练 ③ 配置损坏演练 ④ 休眠补跑演练 ⑤ 连续 3 天自动成功
- **工期**：1.5 天 + 3 天观察

### M5 · 稳定性观察期（真正验收）
- **目标**：用 14 天真实数据证明达到 §2 SLO
- **交付物**：运行报告 + 问题修复 + 参数调优 +（可选）归档浏览页
- **边界**：❌ 冻结功能，只修问题+调参
- **验收**：14 天内 ≥ 13 天按时送达、0 重复推送、0 内容丢失、日均成本 ≤ ¥1
- **工期**：14 天（被动观察，开发量 ≈ 1 天）

---

## 5. 质量保障与多 Agent 测试门禁

### 5.1 角色与职责边界

| 角色 | 担任者 | 职责 | 写入范围（硬边界） |
|---|---|---|---|
| 主控/开发 | 主会话 | 架构、编码、修复、编排 | `src/**`、配置、脚本、README |
| 测试 Agent | 每阶段冷启动独立 subagent | 独立验证、对抗测试、出报告、**裁决 PASS/FAIL** | `reports/**`、`tests/independent/**` |
| 调研 Agent | 按需并行 | 只读调研（源结构/API/反爬） | 无写入 |
| 代码审查 Agent | M5 前一次 | 安全/边界/坏味道审查 | 无写入 |

**关键原则**：谁开发谁不能判卷；测试 Agent 不改产品代码；写入范围隔离。

### 5.2 测试 Agent 工作协议

| 原则 | 做法 |
|---|---|
| 冷启动 | 每次全量测试新建会话，不继承开发上下文；只收规格与验收清单 |
| 不信任声明 | 只认「实际命令 + 原始输出」作为证据 |
| 黑盒优先 | 先按验收标准外部验证，再读代码找边界问题 |
| 对抗性 | 坏输入：空值、超长、非法 JSON、断网、重复运行、时间边界、坏配置 |
| 可复现 | 缺陷必须给最小复现步骤 |
| 无歧义裁决 | 争议以可复现证据为准，必要时交用户裁决 |

### 5.3 门禁流程

```
我：开发完成（ruff + pytest 全绿） 
      ▼
测试 Agent（冷启动）：逐项验证 + 对抗测试 → reports/M{n}-test-report.md
      ▼
  PASS ──► 进入下一里程碑
  FAIL(P0/P1) ──► 我修复 ──► 测试 Agent 增量回归 ──► 直至 PASS
```

### 5.4 缺陷分级与放行规则

| 级别 | 定义 | 放行要求 |
|---|---|---|
| P0 阻断 | 崩溃、数据丢失、重复推送、密钥泄露、核心功能不可用 | 必须 0 个 |
| P1 严重 | 影响可用性/体验，可绕过 | 必须 0 个 |
| P2 轻微 | 文案、样式、边缘体验 | 允许遗留，登记 backlog，M5 处理 |

### 5.5 各阶段测试清单

| 阶段 | 测试重点 |
|---|---|
| M0 | 配置校验（缺 key/坏 YAML/非法 URL）、日志脱敏、CLI 行为、工具链门槛 |
| M1 | 单源失败隔离、幂等（连跑 3 次）、24h 边界、URL 规范化、性能 <2min |
| M2 | 聚类准确率 ≥90%、翻译抽检 20 条、坏数据注入、缓存零调用、成本/耗时、单测 |
| M3 | dry-run 三件套、真机推送、图片规格、拆图、链接可点、resend 不重跑 LLM、重复 run 不重推 |
| M4 | 断网/坏 key/坏配置/补跑演练、连续 3 天、日志轮转、告警可操作性 |
| M5 | 14 天数据审计对照 SLO、内容质量抽样、全量回归 |

### 5.6 人工验收点（用户参与）

| 节点 | 用户确认内容 |
|---|---|
| M3 首次真机推送 | 图片美观度、排版清晰度、链接可点性 |
| M4 定时观察期 | 连续 3 天真的 8 点前收到 |
| M5 内容质量 | 14 天日报内容满意度（决定调权重/词表） |

### 5.7 报告留档

```
reports/
├── M0-test-report.md ... M5-test-report.md
└── evidence/            # 日志片段、截图、命令输出
```

报告模板：

```markdown
# M{n} 测试报告
- 时间 / 被测版本 / 环境
- 结论：PASS / FAIL（P0:x, P1:x, P2:x）

## 验收项结果
| # | 验收标准 | 执行方法 | 实际结果 | 判定 | 证据 |

## 缺陷列表
### D1 [P0] 标题
- 最小复现步骤：
- 期望 vs 实际：
- 影响范围：
```

---

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 源被反爬/失效 | 同类源冗余 ≥3；`check` 每日跑源健康度；单源失效降级不阻断 |
| LLM 不稳定/超时 | 重试退避 + 降级（无摘要保留原文）+ 缓存 |
| 电脑关机/休眠 | 任务计划"唤醒计算机"+"错过后尽快启动"；启动补跑 |
| 企微接口变更/限流 | 限 20 条/分钟；失败重试 + 邮件兜底 + 本地存档保底 |
| 内容噪音多 | 预筛词表+权重可配置；观察期按真实数据调优 |
| 成本失控 | 每日 token 统计 + 超阈值告警 |

---

## 7. 凭据与准备清单

| 项 | 状态 |
|---|---|
| 企业微信机器人 webhook | ✅ 已配置（已连通验证） |
| DeepSeek API key | ⬜ 待准备（M2 前） |
| 邮箱 + SMTP 授权码 | ⬜ 待准备（M3 前） |
| 代理地址 | ⬜ 待确认（M4 前，用于 Reddit/HF） |

---

## 8. 时间线

```
M0 ──► M1 ──► M2 ──► M3 ──► M4 ──► M5（14天观察）
0.5+0.5  1.5+1    2+1     2+1     1.5+0.5   1天+14天观察
        （开发+测试修复轮次）

关键路径：M3 端到端打通（首个可用里程碑）
最终验收：M5 观察期 SLO 全部达标
```

---

## 附录 A · 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-09-23 | v1.0 | 需求冻结；模块划分；开发计划；多 Agent 测试门禁机制 |
| 2026-09-28 | v1.1 | 推送时间由「每天 8:00 前」调整为「每天 17:30 前」（任务计划 17:00 触发）；新增 `app.deliver_by` 作为准点率基准 |
| 2026-10-12 | v1.2 | M5 第 1 轮观察期复盘（4/14 送达，代理单点依赖导致静默失败）。修复：① 企微/LLM/邮件/告警**强制直连**，代理仅用于墙外源且**不可用时可降级**；② 预检分级（致命 vs 可降级），代理失败不再拦截每日运行；③ 告警双通道并送"企微 + 邮件"（均直连），都失败写本地 `ALERTS.log`；④ 新增 `aihot skip` 把跳过原因写入 `runs` 表，`aihot report` 区分"跳过"与"没运行"；⑤ 直连客户端 `trust_env=False` 忽略环境变量代理；⑥ `aihot alert` 兜底写文件时给出明确提示。M5 第 2 轮观察期自 2026-10-12 重新计时（至 10-25，14 天） |
| 2026-10-12 | v1.3 | 新增信息源 **GitHub 当日热榜**（`github_trending_page`，网页解析 `github.com/trending`），与既有 Search API 源互补；新增 `docs/PROJECT-REVIEW.md` 项目复盘；建立「改动必更文档」规则（附录 B） |
| 2026-10-12 | v1.4 | 代理健壮性补强：新增**功能性健康检查**（经代理请求 gstatic 204）识别"Clash 端口开着但节点坏"——该场景下走代理的请求会全部 TLS 失败（实测 4 个源受影响），现在自动降级直连；`check --quick` 代理项会显示"端口可连接但节点异常" |
| 2026-10-12 | v1.5 | **内容范围调整为开发者向**：只收录模型突破/开源工具/工程实践/研究进展，不收录硬件资讯与公司间商业消息。**三道过滤 + 一层排序加权**：① `prefilter.exclude_keywords`（内置高精度模式，可追加）；② LLM 提示词明确内容政策（硬件/商业 → 行业动态/融资商业 + importance ≤10）；③ `compose` 排除 `行业动态/融资商业/政策监管` 分类；④ `rank.interest_bonus` 技术类加权、商业类负分（整体替换语义，需列全分类）。真实数据复核见 `docs/evidence/content-policy/2026-10-12-recheck.txt`（24h 78 条原始条目 → 规则层剔除 1 条商业消息、工程内容零误杀） |

---

## 附录 B · 文档同步规则（改动必更）

> 约定：**任何代码/配置/行为改动，必须在同一次提交里同步更新下列文档**；漏更视为未完成。

| 改动类型 | 必须更新 |
|---|---|
| 新增/停用/修改信息源 | `config.yaml` + `config.example.yaml`（注释说明原因）+ `README.md` 信息源表 |
| 新增/修改 CLI 命令、配置项、退出码 | `README.md` 常用命令/配置/退出码 + `PLAN.md` 变更记录 |
| 架构、降级策略、容灾设计变化 | `DESIGN.md`（对应章节）+ `PLAN.md` 变更记录 |
| 里程碑状态、观察期结论 | `README.md` 开发进度表 + `DESIGN.md` §七 现状 + `docs/PROJECT-REVIEW.md` |
| 任何面向用户的行为变化 | `README.md` 排障手册（如涉及）+ `PLAN.md` 变更记录（版本号 +1） |
| 测试/门禁结论 | 由独立测试 Agent 写入 `reports/`（主控不写） |

**自检方法**（提交前执行）：

```powershell
git status                                  # 看是否漏了文档改动
.venv\Scripts\python.exe -m aihot check     # 配置仍然可加载
.venv\Scripts\python.exe scripts\publish_gate.py   # 发布闸门：扫描待提交文件里的密钥/个人邮箱
```

**自动闸门（推荐先装一次）**：

```powershell
git config core.hooksPath scripts/git-hooks   # 启用版本化的 pre-commit hook
```

装好后每次 `git commit` 都会自动跑发布闸门；命中真实密钥/个人邮箱会**直接拒绝提交**（确需跳过用 `--no-verify`，不推荐）。

**版本号规则**：`PLAN.md` 附录 A 的变更记录按 v1.x 递增；行为变化必须写清"改了什么、为什么、影响范围"。
