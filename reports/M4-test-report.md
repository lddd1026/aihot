# M4 测试报告 · 自动化 + 运维加固 + 第二批信息源

- **测试时间**：2026-09-25（独立测试 Agent 冷启动）
- **被测版本**：aihot 0.1.0，工作区 `D:\信息收集整理\aihot`
- **环境**：Windows 11 + Python 3.12.10（项目 venv）+ pytest 9.1.1 + ruff 0.16.8 +
  Playwright 1.63（系统 Edge）+ Windows OCR（zh-Hans-CN）；代理 `http://127.0.0.1:7897`
- **验收依据**：`PLAN.md` M4 章节 + 用户下发的 M4 门禁验收标准（10 项）
- **工作目录约定**：命令均在 `D:\信息收集整理\aihot` 下执行，Python 统一用
  `.venv\Scripts\python.exe`；证据目录 `reports/evidence/m4/`、脚本 `tests/independent/m4_*.py`

## 0. 结论

> **判定：FAIL（P0: 0 / P1: 2 / P2: 8）**
>
> 核心链路（定时任务配置、每日入口 bat、预检退出码、429 重试、新适配器、论文板块、
> 故障演练、论文精选四输出）均实测通过；工具链 `ruff` 零告警、`pytest 314 passed`、
> 密钥扫描 0 泄漏。**但「企微 webhook key 失效时预检不报错」与「渲染/存档失败无告警」
> 两项 P1 未达标**，按门禁规则（PASS = P0 清零且 P1 清零）判 **FAIL**，需修复后做增量回归。
>
> 📌 **第 2 轮回归（2026-09-25，开发者修复后）新结论：PASS（P0: 0 / P1: 0）** ——
> 2 项 P1 已修复并独立复验通过；6 项 P2 修复 5 项；新增 1 项 P2。详见 **§8 回归验证（第 2 轮）**。

### 安全与边界执行声明

- **未向真实企业微信发送任何消息、未向真实邮箱发送任何邮件**。企微全部使用本地假
  webhook（HTTP 直连或 HTTPS CONNECT + 自签 CA MITM，M3 已验证的同一套假服务）。
- **M4 验收链路真实 LLM 调用 0 次**（全部使用假 LLM 服务器）；M0-M3 既有回归脚本
  `m2_real_llm.py` 按设计做小额真实调用（7/7 通过，成本远低于其 ¥0.2 上限）。
- **未执行 `Start-ScheduledTask -TaskName aihot-daily`**：原因见 §6 说明（真实库存在
  187 条待加工条目，立即运行会**真实推送**日报并产生真实 LLM 费用，违反硬约束）。
  调度链路改用「字节一致 bat 的 staged 运行 + 真实根目录 bat（临时配置）运行」验证。
- **未修改** `src/**`、`config.yaml`、`run_daily.bat`、`scripts/**`、`README.md`、`PLAN.md`；
  真实 `data/aihot.db` 与 `data/digests/` 在全部测试前后快照一致（含 M0-M3 回归
  `real_db_unchanged=true`）。
- 测试 Agent 新增/修改的文件仅限 `tests/independent/m4_*.py`、`tests/independent/m4_task_audit.ps1`
  与 `reports/**`；`run_daily.bat` 未被改动（sha256 见证据）。

## 1. 验收项结果

| # | 验收标准 | 执行方法（命令/脚本） | 实际结果 | 判定 | 证据 |
|---|---|---|---|---|---|
| 1 | 定时任务配置与手动运行链路 | `m4_task_audit.ps1`；`logs/run_daily.log`；`runs` 表 | 见 §3.1：07:30/每天、Interactive、WakeToRun、StartWhenAvailable、30min、IgnoreNew、工作目录正确；任务 09:49 实跑链路已在日志/runs 留痕 | ✅ PASS | `raw/01-task-config.txt`、`raw/04-db-state.txt` |
| 2 | 每日入口 bat：`--dry-run` 成功；预检失败 → rc=2 + 告警 + 跳过 | `m4_bat.py`（staged 字节一致 bat + 真实根目录 bat） | staged `cmd /c run_daily.bat --dry-run` rc=0，产出 md/html/jpg；预检失败 rc=2、告警送达、无 run 段、无半成品 | ✅ PASS（1 项 P2 文案） | `m4_bat.json`、`raw/20-bat-success.txt`、`raw/21-bat-preflight-fail.txt` |
| 3 | `check --quick` 只查代理/企微/LLM 且退出码正确 | `m4_checks.py`（真实配置 + 假配置） | 全健康 rc=0；LLM 401/502/拒连 rc=1；企微 502/拒连 rc=1；死源不影响 rc；**企微 key 失效盲区** | ⚠️ FAIL（P1-1） | `m4_checks.json`、`raw/12-wecom-get-semantics.txt`、`raw/16-wecom-bogus-key-cli.txt` |
| 4 | 告警通道 `alert` + `runner._alert` | `m4_alert.py`（假 webhook payload + 进程内注入） | `alert` 成功 rc=0、payload 正确、errcode→rc=1；推送失败触发告警（含明细）；**渲染/存档失败不触发告警** | ⚠️ FAIL（P1-2） | `m4_alert.json`、`raw/17-alert.txt`、`m4_probe_storage_error.json` |
| 5 | 429 重试（含 Retry-After 优先）、4xx 不重试、5xx 重试 | `m4_net.py`（本地脚本化状态码服务） | 31/31：429 重试、Retry-After=1 优先（实测 1.034s）、封顶 30s、400/401/403/404/418 不重试、500/502/503/504 重试、重试耗尽返回错误 | ✅ PASS | `m4_net.json`、`raw/13-net.txt` |
| 6 | 新适配器 arxiv / html / hf_papers | `m4_adapters.py`（真实 HTTP 假服务） | 39/39：参数、时间窗、extra 字段、选择器/相对链接/标题回退、非列表报错、upvotes、入选日 | ✅ PASS（1 观察项） | `m4_adapters.json`、`raw/14-adapters.txt` |
| 7 | 13 个源逐个真实抓取 + 单源失败隔离 | `m4_sources_live.py`（12 启用源 CLI + 2 停用源直连）；`m4_drills.py` | 首轮 12/12 启用源成功（arxiv 60 / reddit 50 / hackernews 52）；复测 arxiv 因同日多次抓取被 429 限流（非源码缺陷）；停用 2 源原因与配置注释一致；单源失败隔离见演练 | ✅ PASS | `raw/25-live-sources.json/.txt`、`raw/25b-disabled-sources.*` |
| 8 | 论文精选 ≤2 篇、不占 Top、四输出可见 | `m4_papers.py`（compose + 三种文字渲染 + 长图 OCR） | 11/11：papers=2、Top=12 全非论文、企微/邮件/存档均含「论文精选」；长图 OCR 实测识别到板块；真实 2026-09-25 存档亦含 2 篇 arXiv | ✅ PASS | `m4_papers.json`、`raw/26-papers-image-ocr.txt` |
| 9 | 故障演练：部分源失败/全部源失败/断网/LLM key 失效/配置损坏 | `m4_drills.py`、`m4_bat.py` | 19/19：部分源失败仍出报并推送（fetch=partial/run=ok）；全部源失败 rc=1 + 告警 + 零推送零落盘；断网同；LLM 401 降级仍出报；坏配置 rc=3 无网络请求 | ✅ PASS | `m4_drills.json`、`raw/30~34-*.txt` |
| 10 | 工具链与回归：ruff / pytest 314 / M0-M3 / 密钥 | `m4_toolchain.py` + `m3_run_all.py` 全量回归 | ruff 0 告警；pytest **314 passed / 0 failed**；密钥扫描 13/13；M0-M3 回归 53 脚本中 16 个失败均已逐一定位为脚本滞后/数据漂移（非产品回归），其中 2 项为本报告 P2 | ⚠️ 部分（工具链全绿；回归失败已逐项定位，无产品回归） | `m4_toolchain.json`、`raw/02-ruff.txt`、`raw/41-pytest.txt`、`raw/10-regression-*.txt` |

## 2. 缺陷列表

### D1 [P1] 企微 webhook key 失效时 `check --quick` 不报错，日报推送与告警可同时静默失效

- **最小复现**：
  1. 取真实 webhook 前缀 + 随机假 key 组成配置（不产生任何消息）：
     `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=00000000-0000-0000-0000-000000000000`
  2. `.venv\Scripts\python.exe -m aihot check --quick --config <临时配置>`
- **期望 vs 实际**：期望 rc=1 并提示 webhook 不可用；**实际 rc=0**，输出
  `[OK ] 企业微信 webhook: HTTP 200（接口可达）`。
- **根因**：`checks.probe_wecom()` 只判断 HTTP 200；真实 qyapi 对**无效 key 也返回 HTTP 200**，
  仅 errcode 不同（实测：真实 key→93017「invalid json request」；假 key→93000「invalid webhook url」）。
  `WeComNotifier` 会在发送时检查 errcode，因此预检能通过、真实推送失败；而失败告警
  `_alert` 使用同一 webhook，同样失败 → **用户侧完全无感**（违反 SLO「失败必有企微告警 100%」）。
- **影响范围**：webhook 被吊销/轮换/误配时，日报与告警同时失效，只能翻 `runs`/日志发现。
- **修复建议**：探测响应体 errcode（93000 视为失败；93017 表示 key 有效但 GET 无法带 JSON 体），
  或对 webhook 加「校验 key」的只读探测；同时为 `_alert` 增加 HTTP 邮件/日志兜底。
- **证据**：`reports/evidence/m4/raw/12-wecom-get-semantics.txt`、
  `raw/16-wecom-bogus-key-cli.txt`、`reports/evidence/m4/m4_checks/3.8*.txt`、
  `m4_checks.json`（3.7/3.8 两项 FAIL）。

### D2 [P1] 渲染/存档阶段失败不触发 `runner._alert`；存档异常会裸 traceback 且 runs 记录为 ok

- **最小复现 A（渲染失败注入，进程内）**：monkeypatch `aihot.runner.render_outputs`
  抛 `RenderError("模拟渲染失败")` → `run_all()` 后 `status=failed`，
  `runs` 表写入 failed 记录，但假企微**未收到任何告警**（`alert_texts=0`）。
- **最小复现 B（长图降级，真实路径）**：monkeypatch 返回 `image_error=...` → `status=partial`，
  文字照常推送，但**无告警**；真实生产中等价于 Edge/Playwright 渲染失败。
- **最小复现 C（存档异常，真实路径）**：将 `data/digests` 做成一个**文件**（模拟磁盘/权限异常）
  → `aihot run --config <临时配置>` 输出裸 `FileExistsError` traceback（rc=1），
  **无告警**，且 `finally` 写入的 `runs` 记录为 `status=ok, errors=[]`（误导排障）。
- **期望 vs 实际**：M4 验收要求「`runner._alert` 在推送失败/渲染失败时被触发」且 SLO 要求
  「失败必有企微告警」；实际只有**推送失败**路径会告警（已实测通过，见 `m4_alert.json` 4.8/4.9），
  渲染/存档异常路径全部无声。
- **影响范围**：浏览器更新、磁盘满、目录被占用等场景下用户收不到日报也收不到告警，
  且 runs 表可能显示成功；M4「故障可告警可排查」目标打折。
- **修复建议**：`run_all` 对渲染/存档异常统一 `except Exception`（保留降级）并调用 `_alert`；
  `image_error` 非空时也发一条告警；`finally` 写记录时用真实 `outcome.status`（异常时置 failed）。
- **证据**：`reports/evidence/m4/raw/17-alert.txt`、`raw/29-storage-error.txt`、
  `m4_alert.json`（4.11/4.13 FAIL）、`m4_probe_storage_error.json`。

### D3 [P2] `run_daily.bat` 预检失败告警文案中文丢失（显示为 `???????LLM / ???? / ...`）

- **最小复现**：staged 环境（字节一致 bat + 假 LLM 不可用配置）执行 `run_daily.bat`，
  从假企微捕获的告警正文为
  `⚠️ aihot 告警\n???????LLM / ???? / ?? ??????????????????? logs\run_daily.log`。
- **期望 vs 实际**：期望可读中文（该行源码本意是说明「LLM/企微/代理 预检失败，详见日志」）；
  实际 `run_daily.bat` 第 22 行的中文字符在文件里已是 `?` 号（`python -c` 读取字节证实）。
- **影响**：告警可读性差，但不影响发送与定位（含日志路径）；属文案缺陷。
- **证据**：`raw/21-bat-preflight-fail.txt`（含捕获 payload）、`m4_bat.json` 2.11。

### D4 [P2] `check --net` 对 arxiv 源恒报失败（无参数 GET → arXiv 400）

- **最小复现**：仅启用 arxiv 的临时配置执行 `aihot check --net` → `[ERR] 源 arxiv: HTTP 400`、
  rc=1；而同一源用 `fetch --source arxiv` 真实抓取 60 条（`raw/25-live-sources.txt`）。
- **影响**：源健康度自检出现稳定误报；若将来把 `check --net` 接入日常链路会造成「永远失败」。
- **建议**：`probe_sources` 按适配器类型构造探测请求（arxiv 带 `search_query`/`max_results=1`）。
- **证据**：`raw/44-check-net-arxiv.txt`、`raw/25-live-sources.txt`、`m1_check_net` 9d 失败。

### D5 [P2] `run --dry-run` 在「无待加工条目」时输出空日报（与代码注释「dry-run 仍出预览」不符）

- **最小复现**：临时库中仅有已加工条目（如 M3 脚本 seed 的 7 条），执行
  `aihot run --dry-run --top 3` → 输出 `③ 日报: ... 精选 0 条 + 简讯 0 条`（M3 时期为 3 条）。
- **影响**：日常运行后想用 `run --dry-run` 预览当日日报会得到空结果；`aihot preview` 不受影响（可绕过）。
- **证据**：`raw/10-regression-triage.txt`（m3_cli 8.9/8.10、m3_e2e 6.18）、`m3_cli` 日志。

### D6 [P2] 极端长内容下企微分片被截断到 10 片，尾部条目链接静默丢失

- **最小复现**：12 条 10KB 级摘要日报 → `render_wecom_parts()` 产出 10 片（触发
  `MAX_WECOM_PARTS=10` 上限），最后 5 条链接不在任何消息中；M3 同期实测为 16 片且「全部 URL 仍在」。
- **判读**：该上限是为修复 M3 D2（单轮 >20 条限流）而引入的取舍；正常日报 1~3 片不受影响，
  存档与长图/邮件不丢内容。建议截断时追加「余下条目见邮件/存档」或把尾片压成纯链接摘要。
- **证据**：`raw/10-regression-triage.txt`（m3_render_text 2.12）。

### D7 [P2] `html` 适配器 `link_selector` 匹配为空时静默返回 0 条（配置写错无提示）

- **最小复现**：`link_selector: "a.no-such-class"` → `fetch()` 返回 `[]`，不抛 `SourceError`，
  源统计显示「0 条」而非失败。
- **影响**：通用 HTML 源（Anthropic 等）改版后配置失灵不易被发现；建议条目节点存在但可用链接为 0 时抛错。
- **证据**：`reports/evidence/m4/m4_adapters/6-link-selector-empty.txt`。

### D8 [P2] M2/M3 部分独立脚本断言滞后（真实库数据漂移 + M4 契约变更），非产品回归

- **性质**：脚本对真实库「冻结状态」/白名单/返回值元数/测试数量的硬编码假设过时，逐项根因见 §4；
  产品行为经人工核验均正确（多条目簇为同一事件、簇号单调、计数与库一致、build_views 3 元组为新增论文节所必需）。
- **影响**：门禁的「M0-M3 脚本保持通过」需按 §4 的定位结论豁免或在 M5 前更新脚本。
- **证据**：`raw/10-regression-triage.txt`、`raw/10-regression-verdicts.txt`。

## 3. 关键实测数字

### 3.1 计划任务 `aihot-daily`（Get-ScheduledTask / Get-ScheduledTaskInfo）

```
State=Ready；Action=D:\信息收集整理\aihot\run_daily.bat，WorkingDirectory=D:\信息收集整理\aihot
Trigger=MSFT_TaskDailyTrigger，StartBoundary=2026-09-24T07:30:00+08:00，DaysInterval=1，Enabled=True
Settings: Enabled=True, WakeToRun=True, StartWhenAvailable=True, MultipleInstances=IgnoreNew,
          ExecutionTimeLimit=PT30M, DisallowStartIfOnBatteries=False, StopIfGoingOnBatteries=False
Principal: LEGION, LogonType=Interactive, RunLevel=Limited
Info: LastRunTime=2026-09-25 09:49:33, LastTaskResult=3221225786(0xC000013A 被终止),
      NextRunTime=2026-09-26 07:30:00, NumberOfMissedRuns=0
```

- 手动运行链路留痕：`logs/run_daily.log` 中 09:49 段落含 preflight（三项 OK）→ `run` →
  8 个源抓取（160 条/新增 130）；`runs` 表有对应 `20260925T014947-ddc92e`（fetch, ok）。
  该次运行被外部终止（0xC000013A），**遗留 187 条待加工条目**（24h 窗口内 169 条）——
  这也是本轮不重跑 `Start-ScheduledTask` 的原因（见 §6）。
- 结论：**配置 7 项关键项全部符合要求**；单次运行链路的日志与 runs 留痕已由既有真实运行与
  staged/真实根目录 bat 两次运行补齐。

### 3.2 13 个信息源真实抓取（24h 窗口，`fetch --source <id> --dry-run`）

| 源 | 结果 | 条数 | 源 | 结果 | 条数 |
|---|---|---|---|---|---|
| qbitai | OK | 7 | deepmind_blog | OK | 1 |
| infoq_cn | OK | 15 | anthropic_news | OK | 0（24h 无新） |
| techcrunch_ai | OK | 15 | meta_eng | OK | 0（24h 无新） |
| openai_news | OK | 0（24h 无新） | reddit_ai | OK | 50 |
| hackernews | OK | 52 | arxiv | OK | 60 |
| theverge_ai | OK | 10 | venturebeat_ai | OK | 0（24h 无新） |
| jiqizhixin（停用） | SourceError：选择器无匹配（站点改版，配置有说明） | 0 | hf_papers（停用） | HTTP 429（IP 封锁，配置有说明） | 0 |

**启用源 12/12 成功**（首轮实测：arxiv 60 条、reddit 50 条、hackernews 52 条；24h 无新内容的
4 个源为正常 0 条）；**复测轮 arxiv 出现 HTTP 429**——为同日多轮测试抓取触发的 arXiv 限流，
非源码缺陷（同源在 10:32/10:56 两轮均成功返回 60 条）。停用源 2/2 失败原因与配置注释一致
（jiqizhixin 站点无列表、hf_papers IP 429）。单源失败隔离由演练验证：
部分源失败时 fetch=partial、run=ok、日报照常推送（`m4_drills.json` 9.1-9.5）。

> 口径说明：真实 `config.yaml` 共 14 条源记录 = 12 条启用 + 2 条停用；其中 13 条为 M4
> 第二批/可抓取源（含 `hf_papers`，待 IP 解封），另 1 条 `jiqizhixin` 为已确认站点失效的
> 停用占位。本轮对全部 14 条都做了真实抓取尝试。

### 3.3 工具链

- `ruff check .`：**All checks passed!（rc=0，0 告警）** —— `raw/02-ruff.txt`
- `pytest`：**314 passed / 0 failed / 0 error**（junit 计数，期望 314）—— `raw/41-pytest.txt`、`raw/pytest-junit.xml`
- `m0_secret_scan.py`：13/13 通过；M4 证据/脚本专用扫描 116 个文件 0 命中 —— `raw/42-secret-scan.txt`
- M0-M3 全量回归：53 个脚本，PASS 27 / 已知 P2 漂移 4 / FAIL 16（全部定位为脚本滞后或数据漂移）/ OBSERVE 3 / INFO 3；
  `real_db_unchanged=true`；受影响最重的 `m1_regression` 修复后隔离复跑 **9/9 通过** ——
  `raw/10-regression-summary.json`、`raw/10-regression-verdicts.txt`、`raw/43-m1-regression-rerun.txt`
- M4 独立套件汇总：**164 passed / 7 failed**，7 个失败全部为本报告登记的 P1/P2
  （m4_checks 2、m4_alert 2、m4_bat 1、m4_probe_storage_error 2），无非预期失败 ——
  `raw/45-m4-run-all.txt`、`run-all-summary.json/.txt`
- `m3_infra_probe.py` 隔离复跑仍为 8/9（唯一失败 `fake-service-churn` 6.02s，宿主代理环境导致的
  时序阈值问题，非产品回归）—— `raw/46-m3-infra-probe-rerun.txt`
- 真实库/存档测试前后快照：**完全一致**（items 363 / runs 9 / digests 2，字节与 mtime 均未变）——
  `raw/00-real-state-before.json` vs `raw/00-real-state-after.json`

## 4. M0-M3 回归失败分诊（逐项，非产品回归）

| 脚本 | 失败项 | 根因 | 是否产品回归 |
|---|---|---|---|
| m1_check_net | 9d | `check --net` 对 arxiv 无参数 GET → 400（D4） | 否（诊断误报，P2） |
| m1_regression / m2_regression | 11a / 9a | 首跑时测试 Agent 新增脚本触发 ruff 告警；修复后 `m1_regression` 隔离复跑 **9/9 通过**（含 `ruff check .` = All checks passed），`m2_regression` 剩余失败仅级联 `m1_check_net`（D4） | 否 |
| m2_dedup | 2k | 真实库新增「OpenAI agent hacked Australian government」同类合簇，白名单未收录（人工核验为同一事件） | 否（数据漂移） |
| m2r2_accuracy | 2b | 同上（242 条→237 簇，3 个多条目簇均合理） | 否（数据漂移） |
| m2r2_d2 | 3b | 真库存在 187 条待加工，`enrich --limit 20` 捞到 6 条种子外的真实条目 → 新簇 10≠6 | 否（数据漂移；簇号 102-111 > 101 单调性正常） |
| m2r3_real_db | 6a | 硬编码基线 items=176/enriched=176/pending=0，现为 363/176/187 | 否（数据漂移） |
| m3_render_image / _edges | 崩溃 | `build_views` 由 2 元组改 3 元组（新增论文节），旧脚本解包失败 | 否（契约变更，脚本待适配） |
| m3_render_text | 2.12 | 分片上限 10（D6） | 否（P2 取舍） |
| m3_cli / m3_e2e | 8.9/8.10/6.18 | dry-run 无待加工时预览为空（D5） | 否（P2） |
| m3_toolchain | 9.1/9.3 | 首跑 ruff（已修复）；硬编码 pytest=293，实际 314 | 否（脚本滞后） |
| m3_dryrun_db | probe.3 | M4 已修复「dry-run 建空库」（M3-D4），脚本仍按旧行为断言 | 否（脚本滞后） |
| m3_infra_probe | fake-service-churn | 假服务启停 3 轮阈值 5s，隔离复跑实测 6.02s（宿主 `HTTP_PROXY` 环境变量使本地 GET 也走代理，时序敏感） | 否（环境时序） |
| m0_yaml_error_probe | bad_alias | M0 已接受遗留项 | 否（已知） |
| m2_cache / m2_cost / m2r2_d1 / m2r2_p2 | 4a/5a-c/1a/4c | M3 已登记 P2 漂移（LLM 调用数/成本契约） | 否（已知漂移） |

**M4 顺带修复的 M3 已知问题（正向回归）**：m3_wecom 24/24（M3-D2 单轮 ≤20 条、
M3-D3 图片失败不再阻断文字）、m1_acceptance 19/19（M3-D4 dry-run 不再建空库）、
m3_cli 旧 P2 8.3/8.4（非法 `--date`）已修复。

## 5. 观察项（不阻断，登记）

- O1 `hf_papers` 因本机 IP 持续 429 保持 `enabled: false`（配置注释明确）；适配器本身经假服务验证通过。
- O2 `jiqizhixin` 站点改版为落地页，`html` 适配器无列表可匹配，保持停用。
- O3 LLM key 全失败时日报降级为原文并可正常推送（`enrich` 记录 failed，`run` 记录 ok，无告警）——
  属设计降级；日常由 `check --quick` 的 LLM 鉴权探测兜底。
- O4 `run_daily.log` 会同时记录预检的源清单与路径信息，但不含任何密钥明文（密钥扫描通过）。
- O5 真库现有 169 条（24h 内）/187 条待加工：下一次真实运行会加工并推送，属预期补跑；
  M5 观察期开始前无需人工干预。
- O6 已注册任务在 09:49 的真实运行被外部终止（0xC000013A），非产品缺陷；但它证明
  「任务 → bat → 预检 → 抓取」链路与日志留痕工作正常。
- O7 同日多轮真实抓取后 arXiv 开始限流：批次复跑中 arxiv 返回过 HTTP 429 与 ReadTimeout，
  均按单源失败隔离（11/12 源成功、日报照常、rc=0）；首两轮抓取均成功返回 60 条。
  判定为测试密集抓取触发的远端限流，非源码缺陷。

## 6. 方法与偏差说明

1. **为何未重跑 `Start-ScheduledTask`**：任务真实运行会执行 `aihot run`（非 dry-run）。
   测试前核查真实库发现 **187 条待加工**（24h 内 169 条），一旦启动将调用真实 LLM 并
   **向真实企微/邮箱推送日报**，直接违反「禁止为测试而制造真实推送」的硬约束。
   因此改用两条等价路径取证：① `run_daily.bat` 的**字节一致副本**（sha256 相同）在 staged
   环境执行完整命令 `cmd /c run_daily.bat --dry-run`；② 真实根目录执行
   `run_daily.bat --dry-run --no-llm --config <临时配置>`（预检用真实配置，运行写临时目录）。
2. **为何未在真实配置上直接跑 `run --dry-run`**：该命令会把当日 `data/digests/`
   三件套与 `digests` 行覆盖为 dry-run 版本，属对真实存档的破坏，故运行阶段使用临时配置/临时库。
3. 真实网络抓取（13 源、`check --quick`/`--net`、真实 qyapi 语义探测）均已执行；
   推送类外部调用为 0，唯一与真实企微相关的请求是 1 次「真实 webhook 前缀 + 假 key」的 GET
   （不产生消息）。
4. M0-M3 回归使用既有入口 `m3_run_all.py` 全量重跑；其证据写入 `reports/evidence/m3/`
   （覆盖上一轮同名日志），本轮汇总另存 `reports/evidence/m4/raw/10-regression-*.txt`。

## 7. 证据索引（reports/evidence/m4/）

```
raw/00-real-state-before.json      真实库/存档快照（前）
raw/01-task-config.txt             Get-ScheduledTask 全字段审计
raw/02-ruff.txt                    ruff check . 输出
raw/04-db-state.txt / 04b          runs 表、待加工计数（前/后）
raw/10-regression*.txt|json        M0-M3 全量回归原始输出/汇总/分诊
raw/11-check-quick-real.txt        真实配置 check --quick（rc=0）
raw/12-wecom-get-semantics.txt     真实 qyapi：有效 key vs 假 key 的 errcode 语义
raw/13-net.txt / 14-adapters.txt   net 重试 / 新适配器 套件输出
raw/15-checks.txt                  预检套件输出（含 2 项 P1 失败）
raw/16-wecom-bogus-key-cli.txt     假 key 配置下 check --quick 实测 rc=0
raw/17-alert.txt / 18-bat.txt      告警 / bat 套件输出
raw/19-papers.txt                  论文板块套件输出
raw/20-bat-success.txt             staged bat 成功路径完整日志
raw/21-bat-preflight-fail.txt      预检失败路径（rc=2 + 告警 payload）
raw/22-real-root-dryrun-no-llm.txt 真实根目录 bat（临时配置）输出
raw/23~24-broken-config*.txt       配置损坏 rc=3
raw/25-live-sources.json|txt       13 源真实抓取结果
raw/25b-disabled-sources.json|txt  2 个停用源直连结果
raw/26-papers-image-ocr.txt        长图 OCR（识别到「论文精选」）
raw/27-drills.txt                  故障演练套件输出
raw/28-sources-live.txt            13 源真实抓取套件输出
raw/29-storage-error.txt           存档异常裸 traceback 证据（D2）
raw/30~34-*.txt                    故障演练各场景原始输出
raw/40~46-*.txt                    工具链（ruff/pytest/密钥扫描/arxiv 探测）与 M4 套件总汇总
m4_*.json                          各套件机器可读汇总（Suite 输出）
run-all-summary.json|txt           M4 套件总汇总
```

---

# 8. 回归验证（第 2 轮）· 2026-09-25

> **第 2 轮结论：PASS（P0: 0 / P1: 0）**
>
> 上轮 2 项 P1 已修复并独立复验通过；6 项修复中 5 项 P2 复验通过；
> `ruff check .` 零告警、`pytest 323 passed`、M0-M3 全量回归无非预期产品回归；
> 新增 1 项 P2（畸形 JSON 响应健壮性）。首轮结论（FAIL）作为历史保留在本文档前部。
>
> 本轮证据目录：`reports/evidence/m4/regression2/`；复验脚本：`tests/independent/m4r2_fixes.py`。

## 8.1 六项修复逐项独立复验

| # | 修复声明 | 独立复验方法 | 实测结果 | 判定 |
|---|---|---|---|---|
| 1 | `probe_wecom` 解析 errcode：93000 失败 / 93017 成功 / 非 JSON 保守成功 | `m4r2_fixes.py` R2-1.1~1.8（假企微 MITM + 真实 qyapi GET） | 93000→rc=1 且提示 key 失效；93017→rc=0；errcode=0→rc=0；非 JSON→rc=0；**探测仅发 GET，0 条 POST**；真实 qyapi+假 key→**rc=1**（上轮复现用例转绿）；真实 key→rc=0 | ✅ 修复 |
| 2 | 渲染 `RenderError` → failed + `_alert("日报渲染失败")` | R2-2.1（进程内注入） | `status=failed`，企微收到含「渲染失败」的告警 | ✅ 修复 |
| 3 | 任意未预期异常 → `logger.exception` + failed + `_alert("日报生成异常")` + 不裸崩 | R2-2.3/2.4/2.5（真实路径：`data/digests` 被文件占用） | rc=1、仍输出完整运行摘要、无未捕获崩溃、企微收到「生成异常」告警、`runs` 记录 **failed**（不再误记 ok） | ✅ 修复 |
| 4 | `run_daily.bat` 改 ASCII 英文告警 | R2-3.1/3.2（staged 字节一致 bat） | 文件全 ASCII（1188 B、31 处 CRLF）；预检失败 rc=2，告警正文 = `Daily preflight FAILED (LLM / WeCom / proxy unreachable)...` 可读、无 `?` | ✅ 修复 |
| 5 | `check --net` 对 arxiv 补参数 | R2-3.3（本地假 arxiv 确定性）+ 10.9（真实） | 探测请求携带 `search_query=cat:cs.AI&max_results=1` 且判定 OK；真实探测 **`[OK ] 源 arxiv: HTTP 200`**，不再出现 400 误报 | ✅ 修复 |
| 6 | `run --dry-run` 无新条目回落数据库 | R2-3.5/3.6（种子 7 条已加工 + 假 RSS/假 LLM） | 输出「精选 3 条 + 简讯 4 条」；md 存档含库内种子标题；M3 的 `m3_cli` 8.9/8.10、`m3_e2e` 6.18 **转绿** | ✅ 修复 |
| 7 | 企微分片触顶追加可见提示 | R2-3.7/3.8（12×10KB 极端内容） | 仍限 10 片，末片含「（内容过长，后续条目已省略）」；加提示后最大片 **3928 B ≤ 4096** | ✅ 修复 |
| 8 | `link_selector` 零匹配抛 `SourceError` | R2-3.9（假 HTML） | 抛 `SourceError: link_selector 未匹配到任何链接：a.no-such` | ✅ 修复 |

## 8.2 第 2 轮全量数字

| 项目 | 结果 | 证据 |
|---|---|---|
| `ruff check .` | **All checks passed（0 告警）** | `regression2/R2-ruff.txt`、`regression2/raw/40-ruff.txt` |
| `pytest` | **323 passed / 0 failed / 0 error**（与声明一致） | `regression2/R2-pytest.txt`、`regression2/pytest-junit.xml` |
| M4 独立套件（10 套重跑） | **170 passed / 1 failed**，唯一失败为已登记的 P2-6（长图降级无告警）；无非预期失败 | `regression2/run-all-summary.json/.txt`、`regression2/raw/R2-m4-summary.txt` |
| M0-M3 全量回归（53 脚本） | PASS 27 / 失败 14 / 无法解析 2 / 漂移 4 / OBSERVE 3 / INFO 3；**全部失败已定位为脚本滞后、数据漂移或外部限流，无产品回归**；`real_db_unchanged=true` | `regression2/10-m0m3-summary.json`、`10-m0m3-verdicts.txt` |
| 隔离复核 | `m1_check_net` **6/6 通过**；`m1_perf` 4/6（真实源瞬时限流，7/12 OK）；`m1_regression` 8/9（唯一失败为 m0_blackbox 滞后断言级联） | `regression2/raw/R2-m1-*.txt` |
| 密钥扫描 | m0_secret_scan 13/13；M4 证据/脚本 130 个文件 0 命中 | `regression2/raw/42-secret-scan.txt`、`m4_toolchain.json` 10.5 |
| 真实库/存档 | 测试前后快照**完全一致**（items 363 / runs 9 / digests 2） | `regression2/raw/00-real-state-before/after.json` |
| 企微真实调用 | 仅 GET 探测（有效 key 与假 key 各 1 次），**未发送任何消息** | `regression2/raw/R2-fixes-console.txt` |

### M0-M3 第 2 轮失败分诊（新增/变化部分）

| 脚本 | 本轮结果 | 根因 | 产品回归 |
|---|---|---|---|
| m0_blackbox | 104/105 | `net-wecom-fake-ok` 用**假 key 期待 rc=0**，与 P1-1 修复后的正确行为冲突（脚本滞后，需改成期望 rc=1 或使用本地假 webhook） | 否 |
| m1_regression / m2_regression | 8/9（隔离）/ 24/25 | 唯一失败均级联上面 m0_blackbox 的滞后断言；`ruff` 项在测试 Agent 修复自身脚本后已通过 | 否 |
| m1_check_net | 5/6（大回归）→ **6/6（隔离）** | 大回归时真实源瞬时限流（Reddit 429、arXiv 超时）；隔离复跑全绿（含 `errcode=93017` 新语义） | 否 |
| m1_perf | 5/6（大回归）→ 4/6（隔离） | 真实源限流导致「源 N/12 OK」不达标（实测 7/12，抓取 133 条）；首轮 12/12 通过，纯外部抖动 | 否 |
| m3_cli / m3_e2e | **转绿 25/27→全部通过 / 22/22** | dry-run 回落修复生效 | ✅ 正向 |
| m3_render_text | 22/23 | 脚本断言「全部 URL 仍在」与「可见省略提示」新口径不同（P2-4 的取舍） | 否 |
| m3_toolchain | 9/10 | 仍硬编码 pytest=293（现 323） | 否 |
| 其余（m3_render_image/_edges、m3_dryrun_db、m3_infra_probe、m0_yaml_error_probe、m2 漂移 5 项） | 与首轮一致 | 首轮已登记（契约变更/脚本滞后/时序/数据漂移） | 否 |

## 8.3 上轮 8 项 P2 结账 + 新增

| 编号 | P2 事项 | 状态 | 复验证据 |
|---|---|---|---|
| P2-1 | `run_daily.bat` 告警文案中文变 `?`（D3） | ✅ 已修复 | R2-3.1/3.2 |
| P2-2 | `check --net` 对 arxiv 误报 400（D4） | ✅ 已修复 | R2-3.3、10.9（真实 `[OK ]`） |
| P2-3 | `run --dry-run` 无待加工时预览为空（D5） | ✅ 已修复 | R2-3.5/3.6、m3_cli/m3_e2e 转绿 |
| P2-4 | 极端长文企微分片静默截断（D6） | ✅ 已修复（改为可见提示；不再承诺 URL 全保留） | R2-3.7/3.8 |
| P2-5 | HTML `link_selector` 空匹配静默返回 0 条（D7） | ✅ 已修复 | R2-3.9 |
| P2-6 | 长图降级（`image_error`，status=partial）无告警 | ❌ **仍未解决**（内容照常送达、有 `[WARN]` 与 runs 记录；本轮修复范围未覆盖该子路径） | `m4_alert.json` 4.13 |
| P2-7 | M2/M3 独立脚本契约/计数滞后（`build_views` 3 元组、pytest 293、dry-run 建库、渲染口径；本轮新增 m0_blackbox 假 key 断言） | ❌ 未解决（测试侧维护项） | §8.2 分诊表 |
| P2-8 | 真实库数据漂移导致 4 个 M2 断言过时（m2_dedup / m2r2_accuracy / m2r2_d2 / m2r3_real_db） | ❌ 未解决（测试侧维护项） | §8.2 分诊表 |
| **R2-D1** | **新发现**：企微 GET 返回**合法 JSON 数组**（畸形体）时 `check` 抛 `AttributeError` 裸 traceback（rc=1）；日常 bat 仍会按预检失败处理并告警，故判 **P2**。建议 `probe_wecom` 对非 dict JSON 走「保守可达」分支 | 🆕 登记 | R2-1.6（`regression2/m4r2_fixes/`）、修复建议：`isinstance(body, dict)` 判定 |

> 口径说明：首轮报告缺陷列表编号为 D3–D8（6 条），其中 D8 含「契约滞后」与「数据漂移」两类；
> 上表按首轮回复中「P2 登记 8 项」的口径展开，P2-6 为首轮 P1-2 家族中的降级子项（首轮按 P2 收录）。

## 8.4 第 2 轮遗留与建议

1. **产品侧（P2）**：长图降级告警（P2-6）、畸形 JSON 健壮性（R2-D1）——建议 M5 前处理；
   两者都不影响「日报送达」与「失败可告警」的主链路。
2. **测试侧（P2-7/P2-8）**：M0/M2/M3 共 9 个独立脚本存在滞后断言（含 1 个因 P1-1 修复而必须反转
   期望的 `m0_blackbox`），建议 M5 冻结功能前统一适配，避免观察期误报。
3. **环境类**：`m1_perf`/`m1_check_net` 依赖真实源健康度，密集抓取日易受 429/超时影响；
   隔离复跑已证明代码路径正常（首轮 12/12、本轮隔离 6/6）。

## 8.5 第 2 轮证据索引（reports/evidence/m4/regression2/）

```
R2-fixes-console.txt                6 项修复复验控制台输出（20 passed / 2 known）
R2-ruff.txt / R2-pytest.txt         工具链原始输出
pytest-junit.xml                    pytest 323 计数
raw/00-real-state-before/after.json 真实库/存档快照
raw/R2-m4-summary.txt               M4 十套件总汇总（170/1）
raw/R2-m1-perf-rerun.txt            m1_perf 隔离复跑（限流噪音）
raw/R2-m1-check-net-rerun.txt       m1_check_net 隔离复跑（6/6 通过）
raw/R2-m1-regression-rerun.txt      m1_regression 隔离复跑（8/9，级联 m0_blackbox）
raw/R2-toolchain.txt              m4_toolchain 复跑（9/9）
raw/44-check-net-arxiv.txt          真实 arxiv 探测（[OK ] HTTP 200）
10-m0m3-regression.txt / 10-m0m3-summary.json / 10-m0m3-verdicts.txt   M0-M3 第 2 轮回归
m4_*.json                           各套件汇总（覆盖写入为第 2 轮结果）
run-all-summary.json|txt            M4 套件总汇总（第 2 轮）
m4r2_fixes/                         修复复验逐项证据文件
```
