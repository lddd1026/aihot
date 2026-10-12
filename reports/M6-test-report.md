# M6 独立验收测试报告（新增信息源 `github_trending_page` + 代理功能性健康检查）

- 验收对象：`D:\信息收集整理\aihot`（工作区变更未提交，`git status` 见"改动范围"）
- 验收类型：**冷启动黑盒独立验收**（只信代码与实测，不信任务书声明）
- 执行人：独立验收测试 Agent（subagent）
- 日期：2026-10-12
- 脚本：`tests/independent/m6r_support.py`、`m6r_trending.py`、`m6r_proxy_health.py`、`m6r_degrade_e2e.py`、
  `m6r_degrade_alert.py`、`m6r_regression.py`、`m6r_run_all.py`；
  第 2 轮复验：`m6r_p2_fixes.py`、`m6r_health_latency.py`
  （上一轮 `m5r_fetch.py` 按新语义**适配**，其余旧脚本未改）
- 证据：`reports/evidence/m6r/`（第 1 轮）、`reports/evidence/m6r/round2/`（P2 修复复验）、
  `reports/evidence/m5r/round3/`（适配后的 m5r 复跑）

## 0. 结论摘要

| # | 待验证声明 | 判定 | 说明 |
|---|---|---|---|
| 1 | 新源能正确解析（夹具 + 真实页面） | **PASS** | 夹具 4 条全字段正确；真实 `github.com/trending` 只读 GET 解析出 8 条 |
| 2 | 新源容错（无条目/改版/403）且单源隔离 | **PASS** | 4 类故障均抛 `SourceError`；坏源失败不影响另一源（6 条照常） |
| 3 | 新源不越界（不写库/不推送/不调 LLM）+ 配置校验 | **PASS** | 适配器独立运行零副作用；接受 `github_trending`、拒绝未知类型 |
| 4 | 健康检查双向正确（204→用代理；403/500/断开→降级） | **PASS** | 正例 3 项 + 反例 4 类 + 逃生门；`probe_proxy` 文案与 fatal 语义正确 |
| 5 | 端到端降级（节点坏仍出报 + WARN + rc=0） | **PASS** | `fetch --dry-run` rc=0、两源 5+4 条、含"降级为直连"；`check --quick` rc=0 且 `[WARN]…节点异常` |
| 6 | 回归（pytest 全绿 / ruff 零告警 / m5r 适配后通过） | **PASS** | 第 1 轮：pytest 390 passed、m6r 5 套件 rc=0；**第 2 轮（修复后）：pytest 393 passed、m6r 7 套件 108 项 0 失败、m5r 8 套件 rc=0** |
| 7 | 文档一致（README/DESIGN/PLAN/PROJECT-REVIEW） | **PASS（有 2 处 P2 文档缺口）** | 降级策略、健康检查描述一致；DESIGN 源表未列 `github_trending`、4.4 表遗留"改用 Search API"表述 |

**放行意见（第 2 轮更新，2026-10-12）：可以提交并发布到 GitHub。**
- 4 项 P2 已全部修复并**复验通过**（§6：P2-1 前缀排除 / P2-2 limit 语义 / P2-3 多探测点健康检查 / P2-4 文档）；
- 修复后全量回归仍全绿：`pytest` **393 passed**、`ruff check .` 无告警、m6r **7 套件 / 108 项检查** 0 失败、m5r 8 套件 0 失败；
- 无 P0/P1；第 1 轮 4 条 P2 已闭环；剩余 P3 均为测试侧卫生观察项（P3-1 已闭环，P3-2/P3-3 不影响功能）。

### 0.1 执行结果总表

| 套件 | 检查项 | 结果 | 证据 |
|---|---|---|---|
| `m6r_trending.py`（声明 1/2/3） | 43 | 43 PASS / 0 FAIL | `reports/evidence/m6r/m6r_trending.json` |
| `m6r_proxy_health.py`（声明 4） | 15 | 15 PASS / 0 FAIL | `reports/evidence/m6r/m6r_proxy_health.json` |
| `m6r_degrade_e2e.py`（声明 5） | 12 | 12 PASS / 0 FAIL | `reports/evidence/m6r/m6r_degrade_e2e.json` |
| `m6r_degrade_alert.py`（声明 5 推送侧补充） | 5 | 5 PASS / 0 FAIL | `reports/evidence/m6r/m6r_degrade_alert.json` |
| `m6r_regression.py`（声明 6） | 5 | 5 PASS / 0 FAIL | `reports/evidence/m6r/m6r_regression.json` |
| **第 1 轮合计（`m6r_run_all.py` 5/5 套件 rc=0）** | **80** | **80 PASS / 0 FAIL** | `reports/evidence/m6r/run-all-summary.json` |
| `m6r_p2_fixes.py`（**第 2 轮：P2 修复复验**） | 26 | 26 PASS / 0 FAIL | `reports/evidence/m6r/round2/m6r_p2_fixes.json` |
| `m6r_health_latency.py`（第 2 轮：最坏耗时观察） | 2 | 2 PASS / 0 FAIL | `reports/evidence/m6r/round2/m6r_health_latency.json` |
| **第 2 轮 m6r 合计（`m6r_run_all.py` 7/7 套件 rc=0，含 `p2_fixes` 追加的真实页面核对）** | **108** | **108 PASS / 0 FAIL** | `reports/evidence/m6r/round2/run-all-summary.json` |

外部回归：`pytest` **393 passed**（rc=0，174s）｜`ruff check .` → `All checks passed!`｜适配后的 m5r 8 套件 rc=0（`reports/evidence/m5r/round3/run-all-summary.json`）。

## 1. 方法与约束遵守

- 全部假服务只在 localhost：假 Trending 页、假代理/假节点、假 RSS、假 LLM、假企微（仅起服务不调用）。
- **唯一外部请求**：对 `https://github.com/trending` 的只读 GET（任务书允许）。
  - 直连**时通时断**（首轮 ReadTimeout，末轮成功）；经本机配置代理 `http://127.0.0.1:7897` 稳定成功（3.5s、200、557KB）。
  - 两种路径最终都验证了同一件事：真实页面能被适配器解析（末轮直连解析出 8 条真实条目）。
- 未修改 `src/**`、`config.yaml`、`run_daily.bat`、真实 `data/`（内容）、`logs/`；未真实推送/发邮件/调 LLM。
- 未修改既有旧脚本（`m0_*`~`m4_*`、`m3/m4_support.py`、`m5r_support.py` 等）；`m5r_fetch.py` 按任务书要求适配本轮健康检查（见 §2.6）。
- 中文输出全部先写 UTF-8 文件再读，避免控制台乱码。

## 2. 逐条声明验证

### 2.1 声明 1：新源能正确解析 — PASS

适配器 `src/aihot/sources/github_trending.py`（type `github_trending`），夹具按 `article.Box-row / h2 a[href] / p /
[itemprop=programmingLanguage] / a[href$="/stargazers"] / span.float-sm-right` 结构构造，共 6 行（2 行 href 不可用）。

| 检查 | 结果 | 关键证据 |
|---|---|---|
| 1.1 6 行 → 恰好 4 条 `RawItem`（坏 href 被跳过） | PASS | `reports/evidence/m6r/m6r_trending/1-items.json` |
| 1.2 请求带 `since=daily`（period 生效） | PASS | 同上（假站点记录 query） |
| 1.1a~1.4c 每条：title=`owner/repo`、url=`https://github.com/owner/repo`、语言/总 star/今日 star/fork 进 `extra` | PASS | 例：`extra={language:'Python', stars:124887, stars_today:25599, forks:8123}` |
| 1.5 描述清洗（去 `<em>` 标签、`&amp;` 还原） | PASS | raw_summary=`A tiny inference engine & toolkit for LLMs ｜ 语言 Python｜star 124,887｜今日 +25,599｜fork 8,123` |
| 1.6 `published_at` 用"发现时间"（窗口内，非页面数据） | PASS | `published=2026-10-12T09:23:40Z` |
| 1.7 `raw_summary` 含元信息（语言/star/今日/fork） | PASS | `1-raw-summaries.txt` |
| `extra.kind == "repo"`、`source_page="trending"`、`trending_period`、`upvotes==stars` | PASS | `1-items.json` |
| 1.8 `limit` 边界：2→2、3→2、0→默认 12（4 条）、25→4、999→夹到 25（4 条） | PASS | 见 P2-2（limit 计"页面行数"） |
| 1.9/1.10 `period=weekly` 生效；非法值回退 daily | PASS | 假站点 query 记录 |
| 1.11/1.12 `config.example.yaml` / 真实 `config.yaml` 的该源均合法且 `enabled: true, period: daily, limit: 12` | PASS | `validate()` 返回 0 error |
| 1.13 真实页面只读解析（\|items\|≥5、字段齐全） | PASS | **8 条**，如 `morluto/rea`、`boykopovar/AnyPS5`；`4-live-github-trending-直连.json` |

### 2.2 声明 2：新源容错 + 单源隔离 — PASS

| 检查 | 结果 | 证据 |
|---|---|---|
| 2.1 空页 → `SourceError("页面未匹配到趋势条目…")` | PASS | `m6r_trending.json` |
| 2.2 结构改版（`article.TrendCard`）→ `SourceError` | PASS | 同上 |
| 2.3 条目全部不可解析（多级 href）→ `SourceError("趋势条目全部解析失败…")` | PASS | 同上 |
| 2.4 HTTP 403 → `SourceError("请求失败: 403")` | PASS | 同上 |
| 2.5 `build_adapters` 识别新类型（不落 skipped） | PASS | adapters=`[github_trending_page, fake_feed]` |
| 2.6 坏源（403）失败被隔离：另一 RSS 源照常 6 条；错误进统计可诊断 | PASS | `2-isolation-items.json` |
| 2.7 进程内 `fetch_stage(dry_run)` 部分失败仍返回条目、`proxy_mode=direct` | PASS | 同上 |
| 另：CLI 级（staged 配置）两条源一好一坏 | PASS | `2.6 …txt`（套件内） |

### 2.3 声明 3：新源不越界 + 配置校验 — PASS

| 检查 | 结果 | 证据 |
|---|---|---|
| 3.1 适配器独立运行只产出 `RawItem`；未创建 DB/日志/任何文件 | PASS | `data_adapter_only 文件=无`（`m6r_trending.json`） |
| 3.2 运行期间假 LLM 0 请求（POST=0、/models=0） | PASS | 同上 |
| 3.3 运行期间假企微 0 请求 | PASS | 同上 |
| 3.4 源码静态核对：不导入 `store/delivery/pipeline/checks` | PASS | 命中导入=无 |
| 3.5 配置校验接受 `github_trending` 且保留 `extra` | PASS | extra=`{period: daily, limit: 12}` |
| 3.6 `ADAPTER_TYPES` 已注册 | PASS | `['arxiv','github','github_trending','hackernews','hf_papers','html','rss']` |
| 3.7 未知类型拒绝（`ConfigError` 且报错列出可选类型） | PASS | `type 不支持 'github_trending_v2'（可选: … github_trending …）` |

### 2.4 声明 4：健康检查双向正确 — PASS（15/15）

| 用例 | 结果 |
|---|---|
| 4.1 假代理返回 204 → `proxy_healthy()` True；且探测确实经代理（代理侧记录 `GET http://…/generate_204`） | PASS |
| 4.2/4.3 健康 → `resolve_proxy()==(url,'proxy')`；`probe_proxy` 为 `[OK ] 127.0.0.1:xxxx 可连接且节点健康` | PASS |
| 4.4 健康代理时 `fetch_stage` → `proxy_mode=proxy`，**目标端口关闭仍抓到 3 条**（只可能经代理） | PASS |
| 4.5/4.6/4.7 一律 403 / 一律 500 / 连上即断开 → `proxy_healthy()` False、`resolve_proxy()` 降级（TCP 可连=True） | PASS ×3 |
| 4.8 端口关闭 → `proxy_reachable()` False + 降级（不抛异常） | PASS |
| 4.9/4.10 未启用 / 空地址 → `direct` | PASS |
| 4.11 `check_health=False` 退化为纯 TCP 探测（不再发健康探测请求） | PASS |
| 4.12/4.13 端口通但节点坏 → `[WARN]「端口可连接但节点异常」`、`fatal=False`；`run_quick_checks` 为可降级 WARN | PASS |
| 4.14 反证：节点坏 + 源只能经代理到达 → `degraded` + 全失败（**不谎报成功**） | PASS |
| 4.15 README/DESIGN 所述"节点健康"文案与实现一致 | PASS |

**测试保真度说明（重要）**：`PROXY_HEALTH_URL` 是模块常量 `https://www.gstatic.com/generate_204`。
在"只连 localhost"的约束下，**正例**在测试进程内用 `unittest.mock.patch.object` 把该常量临时指向本机假代理
（未改 `src/**`）；**反例与 CLI 用例使用真实常量**，失败方向因此是端到端真实的——假节点侧的确记录了
`CONNECT www.gstatic.com:443`（见 `m6r_degrade_e2e` 证据），说明探测真的发起、且因无法建立隧道而被判"节点异常"。
真实代理（`127.0.0.1:7897`）只核对了 TCP 可连（未让它去请求 gstatic，见 §4 风险）。

### 2.5 声明 5：端到端降级 — PASS（12/12）

夹具：`config.proxy` → 本机"假节点"（端口可连、对所有请求返回 403/CONNECT 拒绝），源为两个本地假 RSS。

| 检查 | 结果 | 关键证据 |
|---|---|---|
| 5.1 `aihot fetch --dry-run` rc=0，两源抓到 5+4 条 | PASS | `1-fetch-degraded-bad-node.txt` |
| 5.2 输出含 `代理不可用或节点异常（…），本次采集降级为直连…` | PASS | 同上 |
| 5.3 降级日志点名坏代理地址 + "节点异常" | PASS | 同上 |
| 5.4 dry-run 仍未创建数据库 | PASS | db exists=False |
| 5.5 假节点确实收到 `CONNECT www.gstatic.com:443`（体检真的做了） | PASS | `1b-bad-node-requests.json` |
| 5.6/5.7 `check --quick` rc=0；代理项 `[WARN] 端口可连接但节点异常（健康检查失败，将降级为直连…）` | PASS | `2-check-quick-bad-node.txt` |
| 5.8 LLM 项仍 `[OK ]`（本地假 LLM，未被坏节点牵连） | PASS | 同上 |
| 5.9 反证：假 LLM 关闭 → rc=1、LLM `[ERR]`（预检未变成"永远成功"） | PASS | `3-check-quick-llm-down.txt` |
| 5.10/5.11 回归：代理端口彻底关闭 → rc=0、`[WARN] 连接失败…降级`、fetch 仍抓 5 条 | PASS | `4-check-quick-port-closed.txt`、`5-fetch-port-closed.txt` |
| 5.12 真实库/存档未被改动（前后快照一致） | PASS | 套件 JSON |

**补充（声明 5 的推送侧，本轮自行加验）**：`m6r_degrade_alert.py`（5/5 PASS）用本地假企微做了一次**完整非 dry-run 运行**
（`runner.run_all(dry_run=False, with_image=False)`）：坏节点 → 自动推送文本告警
`⚠️ aihot 告警：运行降级提示 - 代理不可用，今日已降级为直连采集（部分墙外源缺失，日报照常发送）`，
且日报本身送达（`delivered=True`，假 LLM POST=2）。证据：`reports/evidence/m6r/m6r_degrade_alert/1-run-all-degraded.txt`。

### 2.6 声明 6：回归 — PASS

| 检查 | 结果 | 证据 |
|---|---|---|
| pytest 全绿（rc=0、0 失败、用例数 ≥390） | PASS（**390 passed**，59.9s） | `reports/evidence/m6r/m6r_regression/1-pytest.txt` |
| `ruff check .` 零告警 | PASS（rc=0、`All checks passed!`） | `reports/evidence/m6r/m6r_regression/2-ruff.txt`（另：本轮全仓 `ruff check .` 复核一致） |
| **适配后的 `m5r_fetch.py`** 通过 | PASS（rc=0） | `reports/evidence/m6r/m6r_regression/3-m5r_fetch.txt` |
| 全量 m5r 套件（8 个，跳过会连真实域名的 `m4_alert` 回归）全部 rc=0 | PASS | `reports/evidence/m5r/round3/run-all-summary.json` |
| 一键运行器（5 个 m6r 套件）总体 rc=0；80 项检查 0 失败 | PASS | `reports/evidence/m6r/run-all-summary.json` |

> 备注：`m6r_regression.py` 单跑（最后一次，5/5 PASS，pytest 390 passed / ruff clean / m5r 8 套件 rc=0）与上述
> run-all 中的同名套件结果一致；此后仅调整了"m5r_fetch 证据写到 `m5r/round3`"的路径，不影响判定。

**m5r_fetch 适配说明（本轮行为变更，任务书要求）**：旧断言"端口可连即用代理"正是本轮被修掉的行为。
适配后：
- 声明 3 改为**进程内**验证，并在进程内把 `net.PROXY_HEALTH_URL` 指向本机假代理 → 断言
  `proxy_mode == "proxy"`、目标端口关闭仍抓到 8 条、假代理记录了绝对 URI GET（原设计意图不变，判据改为"健康检查通过才用代理"）；
- 新增 3.4：同一夹具走 CLI 真实常量时，假代理因无法完成 https 健康探测被判定不可用 → 降级直连 →
  只经代理可达的源失败、rc=1（本轮**刻意**行为变更，已在脚本头部与报告中注明）。
m5r 其余套件（preflight/round2 等）未做逻辑改动即通过（它们只断言"坏端口 → WARN 降级"，语义未变）。

### 2.7 声明 7：文档一致 — PASS（附 2 处 P2 缺口）

| 文档 | 核对项 | 结论 |
|---|---|---|
| README.md:121-122 | 两个 GitHub 源并列（Search API 近 7 天 + 当日热榜），并说明"网页解析、偶发 403/超时属预期" | 一致 |
| README.md:78/99 | "健康检查判定后自动降级为直连"、"`check --quick` 显示端口可连接但节点异常" | 与实现/实测一致（5.7） |
| DESIGN.md:384 | "代理健康检查：经代理请求 gstatic 204（5 秒超时），失败只降级、不拦截" | 与 `net.py`（5.0s、200/204、只降级）一致 |
| DESIGN.md:371/383、322-332 | 降级矩阵、TCP 快速探测 2 秒 | 一致 |
| PLAN.md:427/428 | v1.3（新源 + PROJECT-REVIEW + 改动必更文档）、v1.4（功能性健康检查 + check --quick 文案） | 一致 |
| docs/PROJECT-REVIEW.md:84/85/154/155 | 双源互补、Trending 页风险、健康检查、"gstatic 可达性可能误判"自述 | 一致（风险已自述，见 P2-3） |
| **DESIGN.md:91（源/模块表）** | 只列 `github`，**未列 `github_trending`** | **缺口（P2-4）** |
| **DESIGN.md:283（4.4 难点表）** | 仍写"GitHub Trending 页 → 改用 Search API" | **缺口（P2-4）** |

## 3. 问题清单（按严重度）

| 级别 | 问题 | 证据 | 影响 / 建议 |
|---|---|---|---|
| **P2-1** | 适配器把 `/sponsors/<user>` 这类**两段路径**当仓库（`REPO_HREF=^/owner/repo$`） | `m6r_trending/2b-sponsors-link-probe.json`（得到 `sponsors/acme-org`） | 真实 Trending 页 h2 恒为 `/owner/repo`，仅在页面结构大变时误报；建议加白名单/排除 `sponsors`、`orgs`、`settings` 等前缀 · **→ 已修复，见 §6 R2-1（PASS）** |
| **P2-2** | `limit` 语义是"页面**行数**上限"而非"有效条目数"：`limit=3` 时第 3 行不可解析 → 只出 2 条 | `m6r_trending.json`（1.8 项） | 配置注释"取热榜前 N 条"易误解；建议解析后截断，或文档注明 · **→ 已修复，见 §6 R2-2（PASS）** |
| **P2-3** | 健康检查固定探测 `https://www.gstatic.com/generate_204`（5s）：若代理规则把 gstatic 直连/被污染、或代理只放行特定域名，会**误判"节点异常"→ 全量降级直连**，墙外源缺失 | `m6r_proxy_health/(4.5-4.7)`、`m5r_fetch 3.4`；`docs/PROJECT-REVIEW.md:155` 已自述 | 不阻断出报（国内源照常），但可能静默丢掉墙外源。建议把探测点做成配置项，或对"代理能连通 github/HN"做一次真源探测 · **→ 已修复（多探测点），见 §6 R2-3（PASS）** |
| **P2-4** | 文档缺口：`DESIGN.md` 源表未列 `github_trending`；4.4 难点表仍写"Trending 页 → 改用 Search API"，与 v1.3 重新引入该源矛盾 | `DESIGN.md:91`、`DESIGN.md:283`（`reports/evidence/m6r/_docs_design.txt`） | 与"改动必更文档"规则冲突；建议补一行说明 · **→ 已修复（DESIGN:92 / DESIGN:284），见 §6 R2-4（PASS）** |
| **P3-1** | ~~非 dry-run 的"运行降级提示"推送未做端到端验证~~ → **已在本轮补验（`m6r_degrade_alert.py` 5/5 PASS）**：坏节点时自动推送告警文本、日报照常送达 | `reports/evidence/m6r/m6r_degrade_alert/` | 已闭环，保留记录 |
| **P3-2** | 测试夹具副作用：`real_state_snapshot()` 以 `mode=ro` 打开真实库时会在 `data/` 生成**空的** `aihot.db-wal`（0 字节）/`aihot.db-shm` | `data/` 目录时间戳；DB 文件本身 mtime 与行数前后一致 | 真实库**内容**未变（各套件快照前后相等已证）；未删除（遵守"不动真实 data/"）；由父会话决定是否清理 |
| **P3-3** | 单独运行 `m5r_fetch.py`（不带 `AIHOT_M5R_EVIDENCE`）会把证据写进 `reports/evidence/m5r/` **根目录**，覆盖 M5 轮的历史证据 | 本轮首次运行即发生；已 `git checkout` 还原，并在 `m6r_regression.py` 里改为写 `m5r/round3` | 测试卫生问题；建议在 m5r 套件头部注明或默认加前缀 |
| **P3-4** | 多探测点健康检查的最坏耗时：节点"挂起"时单点约 `2 × timeout`，默认 5s × 2 探测点 ≈ **20s**（原单点 ≈10s） | `reports/evidence/m6r/round2/m6r_health_latency/1-hanging-node-latency.txt`（timeout=1.0s 实测 3.90s） | 预检与采集各一次的固定开销，不影响正确性；在意可调小 `PROXY_HEALTH_TIMEOUT` 或减探测点 |

无 P0 / P1。P2 共 4 条（P2-1~P2-4），P3 观察 3 条（P3-1 已闭环、P3-2/P3-3 为测试侧卫生问题）。

## 4. 测试保真度与残余风险（供评审参考）

1. **健康检查正例走了"指向本机"的替身探测点**（`PROXY_HEALTH_URL` 进程内 patch），因为真实探测点是
   https 第三方域名，超出本轮"只连 localhost"约束。反例方向用**真实常量**端到端跑通，
   因此"不能永远成功"是硬证据；"健康判定为真"后走代理的整链路（resolve→fetch→probe 文案）也已在进程内验证。
2. **真实代理的"节点健康"未被本轮验证**：本机 `127.0.0.1:7897` TCP 可连、且经它只读 GET
   `github.com/trending` 成功（说明它当前对真实目标是可用的）——但按约束没有让它去请求 gstatic。
   这意味着若用户环境的 gstatic 被特殊路由，健康检查可能给出与实际能力不符的结论（见 P2-3）。
3. **真实页面解析**：直连时通时断（首轮 ReadTimeout、末轮成功），经本机代理稳定成功；
   两种路径合计验证：真实 `github.com/trending` 页面被解析出 **8 条** 真实仓库（`extra.kind=repo`、
   star/今日 star/语言/描述齐全）→ 选择器与 2026-10-12 的真实页面结构匹配。
4. 未触发：**真实**企微推送、**真实**邮件、**真实** LLM（全部用本地假服务）；非 dry-run 的落库与送达
   只在 `m6r_degrade_alert.py` 中以本地假渠道（假企微 + 假 LLM + staged data 目录）验证过。

## 5. 改动范围核对（git status）
- 工作区改动（非本 Agent 所为）：`src/aihot/{net,checks,config,runner}.py`、
  `src/aihot/sources/{registry.py,github_trending.py}`、`tests/unit/{test_m5_fixes.py,test_sources_github_trending.py}`、
  `README.md`、`DESIGN.md`、`PLAN.md`、`config.example.yaml`、`docs/PROJECT-REVIEW.md`
- 本 Agent 新增：`tests/independent/m6r_*.py`、`reports/evidence/m6r/**`
- 本 Agent 修改：`tests/independent/m5r_fetch.py`（任务书要求的适配）、`reports/evidence/m5r/round3/**`（m5r 复跑证据）
- 未触碰：`src/**`、`config.yaml`、`run_daily.bat`、真实 `data/`（内容）、`logs/`

## 6. P2 修复复验（第 2 轮，2026-10-12）

复验脚本：`tests/independent/m6r_p2_fixes.py`（25 项检查）；证据根目录：`reports/evidence/m6r/round2/`。
复验基线：本轮修复在工作区（未提交），第 1 轮验收的结论仍适用于未受影响的路径。

### 6.1 P2-1 保留前缀排除 — **PASS**

夹具：5 行"非仓库两段路径"（`/sponsors/acme-org`、`/orgs/acme-org`、`/settings/profile`、
`/login/oauth`、大小写变体 `/Sponsors/Acme`）+ 3 行合法仓库（含 `sponsors-x/real-repo` 与 `foo/sponsors`）。

| 检查 | 结果 | 证据 |
|---|---|---|
| R2-1.1 保留前缀全部丢弃（解析结果恰为 3 条合法仓库） | PASS | `m6r_p2_fixes/p21-titles.json` |
| R2-1.2 不误伤：`sponsors-x/real-repo`、`foo/sponsors` 保留（精确匹配 owner，非前缀匹配） | PASS | 同上 |
| R2-1.3 正常仓库 `acme-org/model-lab` 照常 | PASS | 同上 |
| R2-1.4 反证：页面只剩保留前缀行 → `SourceError`（不产出脏数据） | PASS | 套件 JSON |
| R2-1.5 **真实页面误杀核对**：真实 Trending 页 8 个 owner 无一撞上 `RESERVED_OWNERS` | PASS | `m6r_p2_fixes/p21-live-owners.txt` |

### 6.2 P2-2 `limit` = 解析成功的前 N 条 — **PASS**

夹具：首行为坏行（多级 href）+ 4 行好行；断言顺序与条数。

| 检查 | 结果 | 证据 |
|---|---|---|
| R2-2.2 **坏行 + 2 条好行，`limit=2` → 2 条**（旧语义只会出 1 条） | PASS | `m6r_p2_fixes.json` |
| `limit=1/3/4/0/25/999` → 1/3/4/4/4/4 条（0 归一默认 12、999 夹到 25） | PASS ×6 | 同上 |
| R2-2.8 反证：页面全是坏行 → `SourceError`（不被 limit 掩盖） | PASS | 同上 |

### 6.3 P2-3 多探测点健康检查 — **PASS**

实现核对：`net.PROXY_HEALTH_PROBES = (gstatic 204→{200,204}, api.github.com/rate_limit→{200,204,403})`，
任一通过即健康（R2-3.1）。

| 检查 | 结果 | 证据 |
|---|---|---|
| R2-3.2 **第一探测点失败（502）、第二点通过（204）→ `proxy_healthy` True、`resolve_proxy` 用代理** | PASS | `p23-requests-second-probe-rescues.json` |
| R2-3.3 两个探测点都被尝试且顺序正确（one→two） | PASS | 同上 |
| R2-3.4 403 语义双向：声明可接受 → True；严格集 → False 并降级（不是"任何响应都算健康"） | PASS | 套件 JSON |
| R2-3.5 全部失败（403+500）→ False + 降级 | PASS | 同上 |
| R2-3.6 第一点通过即短路（只发 1 次探测，成本可控） | PASS | 同上 |
| R2-3.7 **反证（真实常量 + 坏节点）**：False + 降级；两个探测点都被尝试（4 次 CONNECT）、耗时 1.93s | PASS | `p23-real-const-bad-node.txt` |
| R2-3.8/3.9 `probe_proxy` 文案：通过 → `[OK ] …可连接且节点健康`；全失败 → `[WARN] 端口可连接但节点异常`(fatal=False) | PASS ×2 | 套件 JSON |

### 6.4 P2-4 DESIGN.md 文档补齐 — **PASS**

| 检查 | 结果 | 证据 |
|---|---|---|
| R2-4.1 §2.2 模块清单已加入 `github_trending`（`DESIGN.md:92`，与 `registry.ADAPTER_TYPES` 一致） | PASS | `p24-design-lines.txt` |
| R2-4.2 §4.4 已改为"双源互补"（`DESIGN.md:284`），全文不再出现"改用 Search API" | PASS | 同上 |
| R2-4.3 表述与实现一致（Search API + Trending 页解析 + 重试/单源隔离 + 2026-10 重新引入） | PASS | 同上 |

### 6.5 复验轮回归（修复后全量复跑）

> 说明：第 1 轮的两个套件在本轮修复后需要同步更新（它们把"修复前行为"写成了断言/补丁常量）：
> `m6r_trending.py` 的 `limit=3` 期望由 2 条改为 3 条（P2-2 修复后语义）、`/sponsors` 探针改为容忍
> "全部被拒 → SourceError"；`m6r_proxy_health.py`、`m5r_fetch.py` 改为 patch 新常量
> `PROXY_HEALTH_PROBES`（原 `PROXY_HEALTH_URL` 已不存在）。更新后套件分别 43/43、15/15 通过；
> 其余套件无需改动即通过。新增 `m6r_health_latency.py`（补测多探测点的最坏耗时）。

| 项目 | 结果 | 证据 |
|---|---|---|
| `pytest` | PASS（**393 passed**，rc=0，174s） | `reports/evidence/m6r/round2/m6r_regression/1-pytest.txt` |
| `ruff check .` | PASS（`All checks passed!`） | `reports/evidence/m6r/round2/m6r_regression/2-ruff.txt` |
| m6r 套件（7 个，含 `m6r_p2_fixes` 与 `m6r_health_latency`） | PASS（全部 rc=0） | `reports/evidence/m6r/round2/run-all-summary.json` |
| 适配后的 m5r 套件（8 个） | PASS（rc=0） | `reports/evidence/m5r/round3/run-all-summary.json` |

**P3-4（新增观察）多探测点带来的最坏耗时**：节点"挂起"（端口可连但不响应）时，每个探测点实测约
`2 × timeout`，默认 `PROXY_HEALTH_TIMEOUT=5s`、2 个探测点 → 健康检查最坏约 **20s**（原单点约 10s）。
证据：`reports/evidence/m6r/round2/m6r_health_latency/1-hanging-node-latency.txt`（`timeout=1.0s`
实测 3.90s / 2 探测点）。属预检与采集各一次的固定开销，不影响正确性，不构成放行阻塞。

### 6.6 第 2 轮结论

4 项 P2 全部**修复并复验通过**（夹具正向 + 反证双向 + 真实页面零误杀核对），未引入功能性问题；
第 1 轮全部检查在修复后（两处期望同步更新、三处补丁常量同步）全量复跑仍全绿（§6.5 证据）。
`DESIGN.md` 两处文档与实现一致，`pytest` 393 用例全绿，`ruff check .` 零告警。
第 1 轮的 P2 条目已在 §3 标记为"已修复"；P3-2/P3-3 为测试侧卫生事项、P3-4 为性能观察，均不影响放行。

### 6.7 约束遵守（第 2 轮）

- 未修改 `src/**`、`config.yaml`、`run_daily.bat`、真实 `data/`（内容）、`logs/`；未真实推送/发邮件/调 LLM。
- 外部请求仅两类：① 对 `https://github.com/trending` 的只读 GET（真实页面解析 + 误杀核对）；
  ② 通过本机代理 `127.0.0.1:7897` 的同名只读 GET（首轮直连超时时使用）。
- 未修改既有旧脚本（`m0_*`~`m4_*`、`m3/m4_support.py`、`m5r_support.py` 等）；
  `m5r_fetch.py`、`m6r_trending.py`、`m6r_proxy_health.py` 的改动均为**同步修复后的语义/常量**
  （见 §6.5 说明），已在脚本内注明。
